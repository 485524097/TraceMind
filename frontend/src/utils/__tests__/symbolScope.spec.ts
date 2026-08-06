import { describe, expect, it } from 'vitest'

import {
  normalizeSymbolScope,
  symbolScopeFallbackLabel,
  symbolScopeIdentity,
  symbolScopeLabel,
} from '@/utils/symbolScope'

describe('symbol scope presentation', () => {
  it('normalizes exact metadata and prefers signature identity', () => {
    const metadata = {
      symbol_scope_mode: 'exact',
      scoped_symbol_kind: 'method',
      scoped_symbol_qualified_name: 'demo.UserService.source',
      scoped_symbol_signature: 'source(String)',
    }
    expect(normalizeSymbolScope(metadata)).toEqual({
      mode: 'exact',
      reason: null,
      kind: 'method',
      qualifiedName: 'demo.UserService.source',
      signature: 'source(String)',
    })
    expect(symbolScopeIdentity(metadata)).toBe('source(String)')
    expect(symbolScopeLabel(metadata)).toBe('精确符号：source(String)')
  })

  it('maps safe fallback reasons and degrades missing or unknown metadata to none', () => {
    expect(symbolScopeFallbackLabel('not_found')).toBe('符号未找到，已回退普通检索')
    expect(symbolScopeFallbackLabel('ambiguous')).toBe('符号存在歧义，已回退普通检索')
    expect(symbolScopeFallbackLabel('unsupported')).toBe('符号匹配范围过大，已回退普通检索')
    expect(symbolScopeLabel({ symbol_scope_mode: 'unexpected', symbol_scope_reason: 'raw' })).toBeNull()
    expect(normalizeSymbolScope(undefined).mode).toBe('none')
  })
})
