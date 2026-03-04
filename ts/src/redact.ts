/**
 * Security redaction pipeline — port of Python lore.redact.
 *
 * Layers: API keys, JWT, PEM private keys, AWS secret keys, high-entropy,
 * emails, phones, IPs (v4+v6), credit cards (Luhn), custom.
 */

// ── Patterns ────────────────────────────────────────────────────────────

/** API keys — prefix-based */
const API_KEY = new RegExp(
  '\\b(?:' +
    'sk-[A-Za-z0-9]{20,}' +        // OpenAI
    '|AKIA[A-Z0-9]{16}' +           // AWS access key ID
    '|ghp_[A-Za-z0-9]{36,}' +       // GitHub PAT
    '|gh[sor]_[A-Za-z0-9]{36,}' +   // GitHub other
    '|xox[bp]-[A-Za-z0-9\\-]{10,}' + // Slack
  ')\\b',
  'g',
);

/** JWT tokens (three base64url-encoded segments separated by dots) */
const JWT_TOKEN = /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g;

/** PEM private key blocks */
const PRIVATE_KEY_BLOCK =
  /-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+|ENCRYPTED\s+)?PRIVATE\s+KEY-----/g;

/** AWS secret access keys (40-char base64 — only matched when AKIA present on same line) */
const AWS_SECRET_KEY = /(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])/g;

/** Email addresses */
const EMAIL = /\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b/g;

/** Phone numbers (international formats) */
const PHONE = new RegExp(
  '(?<!\\d)' +
  '(?:' +
    '\\+\\d{1,3}[\\s\\-]?' +
  ')?' +
  '(?:' +
    '\\(\\d{2,4}\\)[\\s\\-]?' +
    '|\\d{2,4}[\\s\\-]' +
  ')' +
  '\\d{3,4}[\\s\\-]?\\d{3,4}' +
  '(?!\\d)',
  'g',
);

/** IPv4 */
const IPV4 = new RegExp(
  '\\b(?:(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\.){3}' +
  '(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\b',
  'g',
);

/** IPv6 */
const IPV6 = new RegExp(
  '(?:' +
    '\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b' +
    '|\\b(?:[0-9a-fA-F]{1,4}:){1,7}:' +
    '|::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}\\b' +
    '|\\b(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}\\b' +
    '|::1\\b' +
  ')',
  'g',
);

/** Credit card (broad match, validated with Luhn) */
const CREDIT_CARD = /\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}\b/g;

/** High-entropy string detection threshold */
const ENTROPY_THRESHOLD = 4.5;

// ── Helpers ─────────────────────────────────────────────────────────────

function luhnCheck(number: string): boolean {
  const digits = number.split('').map(Number);
  let checksum = 0;
  for (let i = digits.length - 1, alt = false; i >= 0; i--, alt = !alt) {
    let d = digits[i];
    if (alt) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    checksum += d;
  }
  return checksum % 10 === 0;
}

function shannonEntropy(s: string): number {
  if (!s) return 0;
  const freq: Record<string, number> = {};
  for (const c of s) {
    freq[c] = (freq[c] || 0) + 1;
  }
  let ent = 0;
  for (const count of Object.values(freq)) {
    const p = count / s.length;
    ent -= p * Math.log2(p);
  }
  return ent;
}

// ── Types ───────────────────────────────────────────────────────────────

export type ScanAction = 'pass' | 'mask' | 'block';

export interface Finding {
  type: string;
  value: string;
  start: number;
  end: number;
  action: ScanAction;
}

export interface ScanResult {
  text: string;
  findings: Finding[];
  action: ScanAction;
  blockedTypes: string[];
}

// ── Default actions ─────────────────────────────────────────────────────

const DEFAULT_ACTIONS: Record<string, ScanAction> = {
  email: 'mask',
  phone: 'mask',
  ip_address: 'mask',
  credit_card: 'mask',
  api_key: 'block',
  jwt_token: 'block',
  private_key: 'block',
  aws_secret_key: 'block',
  high_entropy_string: 'block',
};

// ── Pipeline ────────────────────────────────────────────────────────────

export type PatternDef = [RegExp | string, string];

export class RedactionPipeline {
  private readonly ccPattern: RegExp;
  private readonly simpleLayers: Array<[RegExp, string]>;
  private readonly customLayers: Array<[RegExp, string]>;

  constructor(customPatterns?: PatternDef[]) {
    this.ccPattern = new RegExp(CREDIT_CARD.source, CREDIT_CARD.flags);
    this.simpleLayers = [
      [new RegExp(API_KEY.source, API_KEY.flags), 'api_key'],
      [new RegExp(EMAIL.source, EMAIL.flags), 'email'],
      [new RegExp(PHONE.source, PHONE.flags), 'phone'],
      [new RegExp(IPV4.source, IPV4.flags), 'ip_address'],
      [new RegExp(IPV6.source, IPV6.flags), 'ip_address'],
      [new RegExp(JWT_TOKEN.source, JWT_TOKEN.flags), 'jwt_token'],
      [new RegExp(PRIVATE_KEY_BLOCK.source, PRIVATE_KEY_BLOCK.flags), 'private_key'],
    ];
    this.customLayers = [];
    if (customPatterns) {
      for (const [pat, label] of customPatterns) {
        const re = typeof pat === 'string' ? new RegExp(pat, 'g') : new RegExp(pat.source, pat.flags.includes('g') ? pat.flags : pat.flags + 'g');
        this.customLayers.push([re, label]);
      }
    }
  }

  scan(text: string): ScanResult {
    const findings: Finding[] = [];

    // Credit cards (Luhn validated)
    const ccRe = new RegExp(this.ccPattern.source, this.ccPattern.flags);
    let m: RegExpExecArray | null;
    while ((m = ccRe.exec(text)) !== null) {
      const digitsOnly = m[0].replace(/[\s\-]/g, '');
      if (digitsOnly.length >= 13 && digitsOnly.length <= 19 && luhnCheck(digitsOnly)) {
        findings.push({ type: 'credit_card', value: m[0], start: m.index, end: m.index + m[0].length, action: DEFAULT_ACTIONS['credit_card'] ?? 'mask' });
      }
    }

    // Simple pattern layers
    for (const [pattern, ftype] of this.simpleLayers) {
      const re = new RegExp(pattern.source, pattern.flags);
      while ((m = re.exec(text)) !== null) {
        findings.push({ type: ftype, value: m[0], start: m.index, end: m.index + m[0].length, action: DEFAULT_ACTIONS[ftype] ?? 'mask' });
      }
    }

    // AWS secret keys (same-line AKIA proximity)
    const awsRe = new RegExp(AWS_SECRET_KEY.source, AWS_SECRET_KEY.flags);
    while ((m = awsRe.exec(text)) !== null) {
      const val = m[0];
      if (val.startsWith('AKIA')) continue;
      const lineStart = text.lastIndexOf('\n', m.index) + 1;
      let lineEnd = text.indexOf('\n', m.index + val.length);
      if (lineEnd === -1) lineEnd = text.length;
      const line = text.slice(lineStart, lineEnd);
      if (!line.includes('AKIA')) continue;
      findings.push({ type: 'aws_secret_key', value: val, start: m.index, end: m.index + val.length, action: 'block' });
    }

    // High-entropy strings
    const entropyRe = /\b[A-Za-z0-9]{20,}\b/g;
    while ((m = entropyRe.exec(text)) !== null) {
      const val = m[0];
      if (/^[a-z]+$/.test(val)) continue;
      if (!(/[a-zA-Z]/.test(val) && /\d/.test(val))) continue;
      if (shannonEntropy(val) >= ENTROPY_THRESHOLD) {
        findings.push({ type: 'high_entropy_string', value: val, start: m.index, end: m.index + val.length, action: 'block' });
      }
    }

    // Custom patterns
    for (const [pattern, ftype] of this.customLayers) {
      const re = new RegExp(pattern.source, pattern.flags);
      while ((m = re.exec(text)) !== null) {
        findings.push({ type: ftype, value: m[0], start: m.index, end: m.index + m[0].length, action: DEFAULT_ACTIONS[ftype] ?? 'mask' });
      }
    }

    const action: ScanAction = findings.length === 0
      ? 'pass'
      : findings.some(f => f.action === 'block') ? 'block' : 'mask';
    const blockedTypes = findings.filter(f => f.action === 'block').map(f => f.type);

    return { text, findings, action, blockedTypes };
  }

  run(text: string): string {
    const result = this.scan(text);
    if (!result.findings.length) return text;
    // Sort by start asc, then by length desc (prefer longer/earlier matches)
    const sorted = [...result.findings].sort((a, b) =>
      a.start !== b.start ? a.start - b.start : (b.end - b.start) - (a.end - a.start),
    );
    // Deduplicate overlapping findings — keep the first (earliest/longest)
    const deduped: Finding[] = [];
    for (const f of sorted) {
      if (deduped.length === 0 || f.start >= deduped[deduped.length - 1].end) {
        deduped.push(f);
      }
    }
    // Apply replacements back-to-front
    let out = text;
    for (let i = deduped.length - 1; i >= 0; i--) {
      const f = deduped[i];
      out = out.slice(0, f.start) + `[REDACTED:${f.type}]` + out.slice(f.end);
    }
    return out;
  }
}

/** Convenience function: redact sensitive data from text. */
export function redact(text: string, pipeline?: RedactionPipeline): string {
  const p = pipeline ?? new RedactionPipeline();
  return p.run(text);
}
