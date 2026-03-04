import type { Memory, ListOptions } from '../types.js';

/**
 * Abstract store interface for memory persistence.
 */
export interface Store {
  save(memory: Memory): Promise<void>;
  get(memoryId: string): Promise<Memory | null>;
  list(options?: ListOptions): Promise<Memory[]>;
  update(memory: Memory): Promise<boolean>;
  delete(memoryId: string): Promise<boolean>;
  count(options?: { project?: string; type?: string }): Promise<number>;
  cleanupExpired(): Promise<number>;
  close(): Promise<void>;
}
