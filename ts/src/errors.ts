/** Raised when the remote server cannot be reached or times out. */
export class LoreConnectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LoreConnectionError';
  }
}

/** Raised when authentication fails (401/403). */
export class LoreAuthError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LoreAuthError';
  }
}

/** Raised when a memory is not found. */
export class MemoryNotFoundError extends Error {
  readonly memoryId: string;
  constructor(memoryId: string) {
    super(`Memory not found: ${memoryId}`);
    this.name = 'MemoryNotFoundError';
    this.memoryId = memoryId;
  }
}

/** Raised when remember() detects a secret that should be blocked. */
export class SecretBlockedError extends Error {
  readonly findingType: string;
  constructor(findingType: string) {
    super(`Content blocked: ${findingType} detected — remove the secret and retry.`);
    this.name = 'SecretBlockedError';
    this.findingType = findingType;
  }
}

/** @deprecated Use MemoryNotFoundError instead */
export const LessonNotFoundError = MemoryNotFoundError;
