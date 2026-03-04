import { describe, it, expect } from 'vitest';
import { RedactionPipeline, redact } from '../src/redact.js';

describe('RedactionPipeline', () => {
  const pipeline = new RedactionPipeline();

  // Layer 1: API keys
  it('redacts OpenAI API key', () => {
    expect(pipeline.run('key: sk-abc123def456ghi789jkl012mno')).toBe(
      'key: [REDACTED:api_key]',
    );
  });

  it('redacts AWS access key', () => {
    expect(pipeline.run('aws: AKIAIOSFODNN7EXAMPLE')).toBe(
      'aws: [REDACTED:api_key]',
    );
  });

  it('redacts GitHub PAT', () => {
    const ghp = 'ghp_' + 'a'.repeat(36);
    expect(pipeline.run(`token: ${ghp}`)).toBe('token: [REDACTED:api_key]');
  });

  it('redacts Slack token', () => {
    expect(pipeline.run('slack: xoxb-1234567890-abcde')).toBe(
      'slack: [REDACTED:api_key]',
    );
  });

  // Layer 2: Emails
  it('redacts email addresses', () => {
    expect(pipeline.run('contact user@example.com for info')).toBe(
      'contact [REDACTED:email] for info',
    );
  });

  it('redacts emails with plus addressing', () => {
    expect(pipeline.run('mail: user+tag@domain.co.uk')).toBe(
      'mail: [REDACTED:email]',
    );
  });

  // Layer 3: Phones
  it('redacts international phone', () => {
    expect(pipeline.run('call +1 (555) 123-4567')).toBe(
      'call [REDACTED:phone]',
    );
  });

  it('redacts phone without international prefix', () => {
    expect(pipeline.run('call (555) 123-4567')).toBe(
      'call [REDACTED:phone]',
    );
  });

  // Layer 4: IPs
  it('redacts IPv4', () => {
    expect(pipeline.run('server at 192.168.1.100')).toBe(
      'server at [REDACTED:ip_address]',
    );
  });

  it('redacts IPv6 loopback', () => {
    expect(pipeline.run('localhost ::1')).toBe(
      'localhost [REDACTED:ip_address]',
    );
  });

  // Layer 5: Credit cards
  it('redacts valid credit card (Luhn)', () => {
    // 4111 1111 1111 1111 is a valid Luhn test number
    expect(pipeline.run('card: 4111 1111 1111 1111')).toBe(
      'card: [REDACTED:credit_card]',
    );
  });

  it('does not redact invalid credit card number', () => {
    // 1234567890123456 fails Luhn — should NOT be redacted as credit card
    // Use no spaces to avoid phone pattern matching
    expect(pipeline.run('number: 1234567890123456')).toBe(
      'number: 1234567890123456',
    );
  });

  it('redacts credit card with dashes', () => {
    expect(pipeline.run('cc: 4111-1111-1111-1111')).toBe(
      'cc: [REDACTED:credit_card]',
    );
  });

  // Layer 6: Custom patterns
  it('applies custom patterns', () => {
    const custom = new RedactionPipeline([[/ACCT-\d+/, 'account_id']]);
    expect(custom.run('account ACCT-12345 found')).toBe(
      'account [REDACTED:account_id] found',
    );
  });

  // Convenience function
  it('redact() convenience works', () => {
    expect(redact('email: user@test.com')).toBe('email: [REDACTED:email]');
  });

  // Multiple redactions in one string
  it('redacts multiple types in one string', () => {
    const text = 'key sk-abcdefghijklmnopqrst123 email user@test.com ip 10.0.0.1';
    const result = pipeline.run(text);
    expect(result).toContain('[REDACTED:api_key]');
    expect(result).toContain('[REDACTED:email]');
    expect(result).toContain('[REDACTED:ip_address]');
    expect(result).not.toContain('sk-');
    expect(result).not.toContain('user@');
    expect(result).not.toContain('10.0.0.1');
  });

  // Disabled redaction
  it('disabled redaction passes text through', () => {
    // This is tested via Lore constructor with redact: false
    const text = 'key: sk-abcdefghijklmnopqrst123';
    // Pipeline itself always redacts — the Lore class gates it
    expect(pipeline.run(text)).toBe('key: [REDACTED:api_key]');
  });

  // JWT tokens
  it('redacts JWT token', () => {
    const jwt =
      'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';
    expect(pipeline.run(`auth: ${jwt}`)).toBe('auth: [REDACTED:jwt_token]');
  });

  it('does not redact short dotted strings as JWT', () => {
    expect(pipeline.run('version 1.2.3')).toBe('version 1.2.3');
  });

  it('does not redact URLs as JWT', () => {
    expect(pipeline.run('abc123.def456.ghi789')).toBe('abc123.def456.ghi789');
  });

  // PEM private keys
  it('redacts RSA private key header', () => {
    expect(pipeline.run('-----BEGIN RSA PRIVATE KEY-----')).toBe(
      '[REDACTED:private_key]',
    );
  });

  it('redacts EC private key header', () => {
    expect(pipeline.run('-----BEGIN EC PRIVATE KEY-----')).toBe(
      '[REDACTED:private_key]',
    );
  });

  it('does not redact public key header', () => {
    expect(pipeline.run('-----BEGIN PUBLIC KEY-----')).toBe(
      '-----BEGIN PUBLIC KEY-----',
    );
  });

  // AWS secret keys
  it('redacts AWS secret key near AKIA', () => {
    const text = 'AKIAIOSFODNN7EXAMPLE wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY';
    const result = pipeline.scan(text);
    const types = result.findings.map((f) => f.type);
    expect(types).toContain('aws_secret_key');
  });

  it('does not flag AWS secret without AKIA', () => {
    const text = 'secret=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY';
    const result = pipeline.scan(text);
    const types = result.findings.map((f) => f.type);
    expect(types).not.toContain('aws_secret_key');
  });

  it('does not flag AWS secret on different line', () => {
    const text =
      'AKIAIOSFODNN7EXAMPLE\nunrelated\nwJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY';
    const result = pipeline.scan(text);
    const types = result.findings.map((f) => f.type);
    expect(types).not.toContain('aws_secret_key');
  });

  // High entropy
  it('detects high-entropy string', () => {
    const secret = 'Zk9mXpL2vR8nQw4jY6tU0hC3bA7dE5fG';
    const result = pipeline.scan(`key=${secret}`);
    const types = result.findings.map((f) => f.type);
    expect(types).toContain('high_entropy_string');
  });

  it('does not flag low-entropy repeating string', () => {
    expect(pipeline.run('value=00000000000000000000')).toBe(
      'value=00000000000000000000',
    );
  });

  it('does not flag short string as high entropy', () => {
    expect(pipeline.run('hash=a1b2c3d4e5f6')).toBe('hash=a1b2c3d4e5f6');
  });

  // Scan result / block action
  it('scan returns block for API key', () => {
    const result = pipeline.scan('key: sk-abc123def456ghi789jkl012');
    expect(result.action).toBe('block');
    expect(result.blockedTypes).toContain('api_key');
  });

  it('scan returns mask for email', () => {
    const result = pipeline.scan('user@example.com');
    expect(result.action).toBe('mask');
  });

  it('scan returns pass for clean text', () => {
    const result = pipeline.scan('just normal text');
    expect(result.action).toBe('pass');
    expect(result.findings).toHaveLength(0);
  });

  it('JWT triggers block', () => {
    const jwt =
      'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';
    const result = pipeline.scan(`auth: ${jwt}`);
    expect(result.action).toBe('block');
  });

  it('private key triggers block', () => {
    const result = pipeline.scan('-----BEGIN RSA PRIVATE KEY-----');
    expect(result.action).toBe('block');
  });

  // maskedText method
  it('maskedText returns redacted text from scan result', () => {
    const result = pipeline.scan('email: user@example.com');
    expect(pipeline.maskedText(result)).toBe('email: [REDACTED:email]');
  });
});
