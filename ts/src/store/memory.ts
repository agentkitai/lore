import type { Store } from './base.js';
import type { Memory, ListOptions } from '../types.js';

function cloneMemory(memory: Memory): Memory {
  return {
    ...memory,
    tags: [...memory.tags],
    metadata: memory.metadata ? { ...memory.metadata } : null,
  };
}

/**
 * In-memory store for testing. Uses a Map keyed by memory ID.
 */
export class MemoryStore implements Store {
  private memories = new Map<string, Memory>();

  async save(memory: Memory): Promise<void> {
    this.memories.set(memory.id, cloneMemory(memory));
  }

  async get(memoryId: string): Promise<Memory | null> {
    const memory = this.memories.get(memoryId);
    return memory ? cloneMemory(memory) : null;
  }

  async list(options?: ListOptions): Promise<Memory[]> {
    let results = Array.from(this.memories.values());

    if (options?.project != null) {
      results = results.filter((m) => m.project === options.project);
    }
    if (options?.type != null) {
      results = results.filter((m) => m.type === options.type);
    }

    // Sort by createdAt descending
    results.sort((a, b) => b.createdAt.localeCompare(a.createdAt));

    if (options?.limit != null) {
      results = results.slice(0, options.limit);
    }

    return results.map(cloneMemory);
  }

  async update(memory: Memory): Promise<boolean> {
    if (!this.memories.has(memory.id)) return false;
    this.memories.set(memory.id, cloneMemory(memory));
    return true;
  }

  async delete(memoryId: string): Promise<boolean> {
    return this.memories.delete(memoryId);
  }

  async count(options?: { project?: string; type?: string }): Promise<number> {
    let results = Array.from(this.memories.values());
    if (options?.project != null) {
      results = results.filter((m) => m.project === options.project);
    }
    if (options?.type != null) {
      results = results.filter((m) => m.type === options.type);
    }
    return results.length;
  }

  async cleanupExpired(): Promise<number> {
    const now = new Date();
    let count = 0;
    for (const [id, m] of this.memories) {
      if (m.expiresAt && new Date(m.expiresAt) < now) {
        this.memories.delete(id);
        count++;
      }
    }
    return count;
  }

  async close(): Promise<void> {
    this.memories.clear();
  }
}
