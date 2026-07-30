export const DIRECTORY_IMPORT_CONCURRENCY = 3

export const SUPPORTED_DOCUMENT_EXTENSIONS = new Set([
  '.md',
  '.txt',
  '.pdf',
  '.docx',
  '.java',
  '.jsp',
  '.js',
  '.ts',
  '.vue',
  '.sql',
  '.xml',
  '.json',
  '.yaml',
  '.yml',
  '.properties',
  '.py',
])

export const IGNORED_DIRECTORY_NAMES = new Set([
  '.git',
  '.hg',
  '.svn',
  '.idea',
  '.vscode',
  '.mypy_cache',
  '.pytest_cache',
  '.ruff_cache',
  '.venv',
  '__pycache__',
  'build',
  'coverage',
  'dist',
  'node_modules',
  'out',
  'target',
  'vendor',
  'venv',
])

export type DirectoryEntryDisposition = 'ready' | 'ignored' | 'unsupported'

export interface PlannedDirectoryFile {
  file: File
  relativePath: string
  disposition: DirectoryEntryDisposition
}

function normalizedBrowserPath(file: File): string {
  return (file.webkitRelativePath || file.name)
    .replace(/\\/g, '/')
    .split('/')
    .filter(Boolean)
    .join('/')
}

function withoutCommonTopDirectory(paths: string[]): string[] {
  const segments = paths.map((path) => path.split('/'))
  const commonTop =
    segments.length > 0 &&
    segments.every((parts) => parts.length > 1 && parts[0] === segments[0]?.[0])
  return segments.map((parts) => (commonTop ? parts.slice(1) : parts).join('/'))
}

function extension(path: string): string {
  const parts = path.split('/')
  const basename = parts[parts.length - 1] ?? ''
  const dot = basename.lastIndexOf('.')
  return dot > 0 ? basename.slice(dot).toLocaleLowerCase() : ''
}

export function planDirectoryImport(files: File[]): PlannedDirectoryFile[] {
  const paths = withoutCommonTopDirectory(files.map(normalizedBrowserPath))
  return files.map((file, index) => {
    const relativePath = paths[index] ?? file.name
    const directories = relativePath.split('/').slice(0, -1)
    const ignored = directories.some((part) =>
      IGNORED_DIRECTORY_NAMES.has(part.toLocaleLowerCase()),
    )
    return {
      file,
      relativePath,
      disposition: ignored
        ? 'ignored'
        : SUPPORTED_DOCUMENT_EXTENSIONS.has(extension(relativePath))
          ? 'ready'
          : 'unsupported',
    }
  })
}
