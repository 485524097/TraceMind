export type SymbolScopeMode = 'none' | 'exact' | 'fallback'
export type SymbolScopeReason = 'not_found' | 'ambiguous' | 'unsupported' | null

export interface SymbolScopeMetadata {
  symbol_scope_mode?: SymbolScopeMode | string
  symbol_scope_reason?: SymbolScopeReason | string | null
  scoped_symbol_kind?: string | null
  scoped_symbol_qualified_name?: string | null
  scoped_symbol_signature?: string | null
}

export interface NormalizedSymbolScope {
  mode: SymbolScopeMode
  reason: SymbolScopeReason
  kind: string | null
  qualifiedName: string | null
  signature: string | null
}

function safeString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function normalizeSymbolScope(metadata: unknown): NormalizedSymbolScope {
  if (!metadata || typeof metadata !== 'object') {
    return { mode: 'none', reason: null, kind: null, qualifiedName: null, signature: null }
  }
  const value = metadata as Record<string, unknown>
  const mode =
    value.symbol_scope_mode === 'exact' || value.symbol_scope_mode === 'fallback'
      ? value.symbol_scope_mode
      : 'none'
  const reason =
    value.symbol_scope_reason === 'not_found' ||
    value.symbol_scope_reason === 'ambiguous' ||
    value.symbol_scope_reason === 'unsupported'
      ? value.symbol_scope_reason
      : null
  return {
    mode,
    reason: mode === 'fallback' ? reason : null,
    kind: safeString(value.scoped_symbol_kind),
    qualifiedName: safeString(value.scoped_symbol_qualified_name),
    signature: safeString(value.scoped_symbol_signature),
  }
}

export function symbolScopeIdentity(metadata: unknown): string | null {
  const scope = normalizeSymbolScope(metadata)
  return scope.signature || scope.qualifiedName || scope.kind
}

export function symbolScopeFallbackLabel(reason: SymbolScopeReason): string {
  switch (reason) {
    case 'not_found':
      return '符号未找到，已回退普通检索'
    case 'ambiguous':
      return '符号存在歧义，已回退普通检索'
    case 'unsupported':
      return '符号匹配范围过大，已回退普通检索'
    default:
      return '未能启用精确符号限定，已回退普通检索'
  }
}

export function symbolScopeLabel(metadata: unknown): string | null {
  const scope = normalizeSymbolScope(metadata)
  if (scope.mode === 'exact') {
    const identity = symbolScopeIdentity(metadata)
    return identity ? `精确符号：${identity}` : '精确符号'
  }
  return scope.mode === 'fallback' ? symbolScopeFallbackLabel(scope.reason) : null
}
