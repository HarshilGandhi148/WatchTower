import { readFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Plugin } from 'vite';

export function localReviewRequestAllowed(req: IncomingMessage): boolean {
  const remote = req.socket.remoteAddress;
  if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(remote ?? ''))
    return false;
  try {
    const origin = new URL(String(req.headers.origin));
    return (
      origin.protocol === 'http:' &&
      ['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname) &&
      origin.host === req.headers.host
    );
  } catch {
    return false;
  }
}

export function recommendationReviews(envFile: string): Plugin {
  return {
    name: 'watchtower-recommendation-reviews',
    configureServer(server) {
      server.middlewares.use('/api/recommendation-reviews', (req, res) => {
        void saveReview(req, res, envFile);
      });
    },
  };
}

async function saveReview(
  req: IncomingMessage,
  res: ServerResponse,
  envFile: string,
) {
  const reply = (status: number, body: object) => {
    res.writeHead(status, {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    });
    res.end(JSON.stringify(body));
  };
  if (req.method !== 'POST' || !localReviewRequestAllowed(req)) {
    reply(403, {
      error: 'Open WatchTower on this computer to review recommendations.',
    });
    return;
  }
  try {
    let body = '';
    for await (const chunk of req) {
      body += chunk.toString();
      if (Buffer.byteLength(body) > 4096) throw new Error('Invalid review');
    }
    const input = JSON.parse(body);
    if (
      !input ||
      typeof input !== 'object' ||
      Array.isArray(input) ||
      Object.keys(input).sort().join(',') !==
        'assessment_round,decision,report_id,request_id,schema_version,snapshot_id' ||
      input.schema_version !== 1 ||
      ![1, 2].includes(input.assessment_round) ||
      !['approve', 'disapprove'].includes(input.decision) ||
      typeof input.report_id !== 'string' ||
      input.report_id.length > 256 ||
      typeof input.snapshot_id !== 'string' ||
      input.snapshot_id.length > 256 ||
      typeof input.request_id !== 'string' ||
      !/^[a-f0-9-]{36}$/.test(input.request_id)
    ) {
      reply(400, { error: 'Choose a current recommendation and try again.' });
      return;
    }
    // Read only these server settings. No private value is added to client env or responses.
    const settings: Record<string, string> = {};
    for (const line of readFileSync(envFile, 'utf8').split(/\r?\n/)) {
      const match = line.match(
        /^\s*(SPACETIMEDB_HTTP_URL|SPACETIMEDB_DATABASE|SPACETIMEDB_ADMIN_TOKEN)\s*=\s*(.*?)\s*$/,
      );
      if (match) settings[match[1]] = match[2].replace(/^(['"])(.*)\1$/, '$2');
    }
    if (
      !settings.SPACETIMEDB_ADMIN_TOKEN ||
      !settings.SPACETIMEDB_DATABASE ||
      !settings.SPACETIMEDB_HTTP_URL
    )
      throw new Error('Review service configuration');
    const response = await fetch(
      `${settings.SPACETIMEDB_HTTP_URL.replace(/\/$/, '')}/v1/database/${encodeURIComponent(settings.SPACETIMEDB_DATABASE)}/call/review_recommendation`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${settings.SPACETIMEDB_ADMIN_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify([JSON.stringify(input)]),
        signal: AbortSignal.timeout(8000),
      },
    );
    if (!response.ok) {
      reply(409, {
        error:
          'This recommendation changed or was already reviewed. Check its latest status.',
      });
      return;
    }
    reply(200, { report_id: input.report_id, decision: input.decision });
  } catch {
    reply(503, { error: 'Your review has not been saved. Please retry.' });
  }
}
