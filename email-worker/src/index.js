/**
 * Helpdesk Email Worker
 * Receives inbound emails via Cloudflare Email Routing and forwards
 * parsed payloads to the helpdesk webhook to create/update tickets.
 *
 * Deploy:
 *   npm install
 *   wrangler secret put WEBHOOK_SECRET
 *   wrangler deploy
 *
 * Cloudflare Email Routing setup:
 *   1. Add MX records for bitsm.io in Cloudflare DNS
 *   2. Enable Email Routing on the zone
 *   3. Add a catch-all rule: *@bitsm.io → this Worker
 */

import PostalMime from 'postal-mime';

export default {
  async email(message, env, ctx) {
    const to = message.to || '';
    const slug = to.split('@')[0].toLowerCase().trim();

    if (!slug) {
      console.error('Inbound email: could not extract slug from To:', to);
      return;
    }

    // Parse full MIME email
    const rawBuffer = await new Response(message.raw).arrayBuffer();
    const parser = new PostalMime();
    const parsed = await parser.parse(rawBuffer);

    const payload = {
      slug,
      from:        message.from || '',
      to:          message.to  || '',
      subject:     parsed.subject   || '(no subject)',
      text:        parsed.text      || _stripHtml(parsed.html || ''),
      message_id:  parsed.messageId || '',
      in_reply_to: parsed.inReplyTo || '',
    };

    const resp = await fetch(`${env.HELPDESK_URL}/api/webhooks/inbound-email`, {
      method: 'POST',
      headers: {
        'Content-Type':    'application/json',
        'X-Webhook-Secret': env.WEBHOOK_SECRET,
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const body = await resp.text().catch(() => '');
      throw new Error(`Helpdesk webhook ${resp.status}: ${body}`);
    }

    console.log(`Inbound email processed: slug=${slug} subject="${payload.subject}"`);
  },
};

function _stripHtml(html) {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}
