/**
 * Regression tests for the resumeConversation fix.
 *
 * Before the fix, resumeConversation called:
 *   set(s => { s.messages = conv.messages || [] })
 * where conv comes from the list endpoint and has NO messages array.
 * This always produced an empty chat.
 *
 * After the fix, resumeConversation calls api.getConversation(conv.id),
 * sets loading=true immediately, populates messages from the full response,
 * and sets loading=false on success. Inactivity timers start only after a
 * successful fetch.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// -------------------------------------------------------------------------
// Mock the api module BEFORE importing chatStore.
// This ensures the store picks up the mocked version at module load time.
// -------------------------------------------------------------------------
vi.mock('../api/client', () => ({
  api: {
    getConversation: vi.fn(),
    archiveConversation: vi.fn().mockResolvedValue(undefined),
    listConversations: vi.fn().mockResolvedValue([]),
    sendMessage: vi.fn(),
  },
}));

import { api } from '../api/client';
import { useChatStore } from './chatStore';

// Typed handle for the mock
const mockGetConversation = vi.mocked(api.getConversation);

// Minimal conversation object as returned by the LIST endpoint (no messages).
const STUB_CONV = {
  id: 42,
  title: 'Test conversation',
  created_at: '2026-03-24T10:00:00Z',
  updated_at: '2026-03-24T10:00:00Z',
  messages: [],   // list endpoint returns empty or absent messages
};

// Full conversation as returned by the DETAIL endpoint.
const FULL_CONV = {
  ...STUB_CONV,
  messages: [
    { role: 'user' as const, content: 'Hello', sources: [], feedback: null },
    { role: 'assistant' as const, content: 'Hi there', sources: [], feedback: null },
  ],
};


describe('resumeConversation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Reset store state to defaults before each test
    useChatStore.setState({
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
    });
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });


  it('sets loading=true synchronously before the API call resolves', () => {
    // REGRESSION: would be RED before this change
    // Before the fix, resumeConversation did a synchronous set of messages from
    // conv.messages (always empty from the list endpoint). loading was never set
    // to true, and getConversation was never called.
    //
    // This test verifies the new behaviour: loading is true immediately after
    // calling resumeConversation — before any promise has settled.

    // Make getConversation never settle during this test
    mockGetConversation.mockReturnValue(new Promise(() => {}));

    useChatStore.getState().resumeConversation(STUB_CONV as any);

    const state = useChatStore.getState();
    expect(state.loading).toBe(true);
    expect(state.conversationId).toBe(42);
    expect(state.messages).toEqual([]);
  });


  it('populates messages from the API response and sets loading=false on success', async () => {
    // REGRESSION: would be RED before this change
    // Before the fix, messages were taken from conv.messages (empty from the list
    // endpoint), so the chat always started blank even for resumed conversations
    // that had prior messages in the DB.

    mockGetConversation.mockResolvedValue(FULL_CONV as any);

    useChatStore.getState().resumeConversation(STUB_CONV as any);

    // Flush the microtask queue so the .then() handler runs
    await vi.runAllMicrotasksAsync();

    const state = useChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0].content).toBe('Hello');
    expect(state.messages[1].content).toBe('Hi there');
  });


  it('sets error and loading=false when the API call rejects', async () => {
    mockGetConversation.mockRejectedValue(new Error('Network failure'));

    useChatStore.getState().resumeConversation(STUB_CONV as any);

    await vi.runAllMicrotasksAsync();

    const state = useChatStore.getState();
    expect(state.loading).toBe(false);
    expect(state.error).toBe('Network failure');
    // Messages must remain empty — do not show stale data on error
    expect(state.messages).toEqual([]);
  });


  it('does NOT start inactivity timers when the API call fails', async () => {
    // If timers were started after a failed fetch, the inactivity system would
    // eventually archive a conversation the user was never shown — silent data loss.

    mockGetConversation.mockRejectedValue(new Error('Timeout'));

    useChatStore.getState().resumeConversation(STUB_CONV as any);

    await vi.runAllMicrotasksAsync();

    // Advance time past both warning (8 min) and inactivity (10 min) thresholds
    vi.advanceTimersByTime(11 * 60 * 1000);

    const state = useChatStore.getState();
    expect(state.inactivityWarning).toBe(false);
    expect(state.archived).toBe(false);
  });


  it('calls api.getConversation with the conversation id', () => {
    mockGetConversation.mockReturnValue(new Promise(() => {}));

    useChatStore.getState().resumeConversation(STUB_CONV as any);

    expect(mockGetConversation).toHaveBeenCalledOnce();
    expect(mockGetConversation).toHaveBeenCalledWith(42);
  });


  it('starts inactivity timers only after a successful fetch', async () => {
    mockGetConversation.mockResolvedValue(FULL_CONV as any);

    useChatStore.getState().resumeConversation(STUB_CONV as any);

    // Before fetch resolves — timers must not have fired yet
    vi.advanceTimersByTime(9 * 60 * 1000); // 9 minutes
    expect(useChatStore.getState().inactivityWarning).toBe(false);

    // Let the fetch resolve
    await vi.runAllMicrotasksAsync();

    // After successful fetch, advance past the warning threshold (8 min from now)
    vi.advanceTimersByTime(8 * 60 * 1000 + 1);
    expect(useChatStore.getState().inactivityWarning).toBe(true);
  });
});
