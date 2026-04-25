import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '../api/client';
import type { ChatMessage, ChatSource, AIConversation, ChatStreamEvent, MessageFeedback } from '../types';

const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes
const INACTIVITY_WARNING_MS = 8 * 60 * 1000;  // 8 minutes (warning 2 min before)

interface ChatState {
  conversationId: number | null;
  messages: ChatMessage[];
  loading: boolean;
  streaming: boolean;
  statusMessage: string | null;
  error: string | null;
  language: string;
  ticketId: number | null;
  conversations: AIConversation[];
  abortController: AbortController | null;
  inactivityWarning: boolean;
  archived: boolean;

  sendMessage: (query: string) => void;
  stopStreaming: () => void;
  setLanguage: (lang: string) => void;
  setTicketContext: (ticketId: number | null) => void;
  clearChat: () => void;
  loadConversations: () => Promise<void>;
  resumeConversation: (conv: AIConversation) => void;
  submitFeedback: (messageIndex: number, rating: 'positive' | 'negative') => void;
  clearInactivityTimers: () => void;
}

// Timer handles stored outside Zustand (not serializable)
let _inactivityTimer: ReturnType<typeof setTimeout> | null = null;
let _warningTimer: ReturnType<typeof setTimeout> | null = null;

function _clearTimers() {
  if (_inactivityTimer) { clearTimeout(_inactivityTimer); _inactivityTimer = null; }
  if (_warningTimer) { clearTimeout(_warningTimer); _warningTimer = null; }
}

export const useChatStore = create<ChatState>()(
  immer((set, get) => ({
    conversationId: null,
    messages: [],
    loading: false,
    streaming: false,
    statusMessage: null,
    error: null,
    language: 'en',
    ticketId: null,
    conversations: [],
    abortController: null,
    inactivityWarning: false,
    archived: false,

    sendMessage: (query: string) => {
      const { conversationId, language, ticketId } = get();

      // Push user message + empty assistant message for streaming
      set((s) => {
        s.messages.push({ role: 'user', content: query });
        s.messages.push({ role: 'assistant', content: '' });
        s.loading = true;
        s.streaming = true;
        s.statusMessage = null;
        s.error = null;
        s.inactivityWarning = false;
        s.archived = false;
      });

      const controller = api.chatStream(
        {
          query,
          conversation_id: conversationId ?? undefined,
          language,
          ticket_id: ticketId ?? undefined,
        },
        (event: ChatStreamEvent) => {
          switch (event.type) {
            case 'conversation_id':
              set((s) => { s.conversationId = event.conversation_id ?? null; });
              break;
            case 'status':
              set((s) => { s.statusMessage = event.content || null; });
              break;
            case 'text':
              set((s) => {
                s.statusMessage = null;
                const last = s.messages[s.messages.length - 1];
                if (last && last.role === 'assistant') {
                  last.content += event.content || '';
                }
              });
              break;
            case 'sources':
              set((s) => {
                const last = s.messages[s.messages.length - 1];
                if (last && last.role === 'assistant') {
                  last.sources = (event.sources || []) as ChatSource[];
                }
              });
              break;
            case 'escalation':
              // L2 escalation starting — push a new assistant message
              set((s) => {
                s.messages.push({ role: 'assistant', content: '' });
                s.statusMessage = event.content || 'Searching deeper...';
              });
              break;
            case 'done':
              set((s) => {
                s.loading = false;
                s.streaming = false;
                s.statusMessage = null;
                s.abortController = null;
              });

              // Reset inactivity timer after each exchange
              _clearTimers();
              _warningTimer = setTimeout(() => {
                set((s) => { s.inactivityWarning = true; });
              }, INACTIVITY_WARNING_MS);
              _inactivityTimer = setTimeout(() => {
                const { conversationId: cid } = get();
                if (cid) {
                  api.archiveConversation(cid).catch(() => {});
                }
                set((s) => {
                  s.archived = true;
                  s.inactivityWarning = false;
                });
              }, INACTIVITY_TIMEOUT_MS);
              break;
          }
        },
        (error: Error) => {
          set((s) => {
            s.error = error.message;
            s.loading = false;
            s.streaming = false;
            s.statusMessage = null;
            s.abortController = null;
            // Remove empty assistant message on error
            if (s.messages.length > 0 && s.messages[s.messages.length - 1].role === 'assistant' && !s.messages[s.messages.length - 1].content) {
              s.messages.pop();
            }
          });
        },
      );

      set((s) => { s.abortController = controller as any; });
    },

    stopStreaming: () => {
      const { abortController } = get();
      if (abortController) {
        abortController.abort();
        set((s) => {
          s.streaming = false;
          s.loading = false;
          s.statusMessage = null;
          s.abortController = null;
        });
      }
    },

    setLanguage: (lang) => set((s) => { s.language = lang; }),
    setTicketContext: (ticketId) => set((s) => { s.ticketId = ticketId; }),

    clearChat: () => {
      const { abortController } = get();
      if (abortController) abortController.abort();
      _clearTimers();
      set((s) => {
        s.conversationId = null;
        s.messages = [];
        s.error = null;
        s.streaming = false;
        s.loading = false;
        s.statusMessage = null;
        s.abortController = null;
        s.inactivityWarning = false;
        s.archived = false;
      });
    },

    loadConversations: async () => {
      try {
        const convs = await api.listConversations();
        set((s) => { s.conversations = convs; });
      } catch { /* ignore */ }
    },

    resumeConversation: (conv: AIConversation) => {
      const { abortController } = get();
      if (abortController) abortController.abort();
      _clearTimers();
      set((s) => {
        s.conversationId = conv.id;
        s.messages = [];
        s.error = null;
        s.streaming = false;
        s.loading = true;
        s.statusMessage = null;
        s.inactivityWarning = false;
        s.archived = false;
      });

      api.getConversation(conv.id)
        .then((full) => {
          set((s) => {
            s.messages = full.messages || [];
            s.loading = false;
          });

          // Start inactivity timer for resumed conversations (after successful fetch)
          _warningTimer = setTimeout(() => {
            set((s) => { s.inactivityWarning = true; });
          }, INACTIVITY_WARNING_MS);
          _inactivityTimer = setTimeout(() => {
            const { conversationId: cid } = get();
            if (cid) {
              api.archiveConversation(cid).catch(() => {});
            }
            set((s) => {
              s.archived = true;
              s.inactivityWarning = false;
            });
          }, INACTIVITY_TIMEOUT_MS);
        })
        .catch((err: Error) => {
          set((s) => {
            s.loading = false;
            s.error = err.message;
          });
        });
    },

    submitFeedback: (messageIndex: number, rating: 'positive' | 'negative') => {
      const { conversationId } = get();
      if (!conversationId) return;

      // Optimistic update
      set((s) => {
        if (s.messages[messageIndex]) {
          s.messages[messageIndex].feedback = rating;
        }
      });

      api.submitFeedback(conversationId, messageIndex, rating).catch(() => {
        // Revert on failure
        set((s) => {
          if (s.messages[messageIndex]) {
            s.messages[messageIndex].feedback = null;
          }
        });
      });
    },

    clearInactivityTimers: () => {
      _clearTimers();
      set((s) => { s.inactivityWarning = false; });
    },
  }))
);
