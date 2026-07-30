import { describe, expect, it } from 'vitest'

import {
  DIRECTORY_IMPORT_CONCURRENCY,
  planDirectoryImport,
} from '@/services/directoryImport'

function directoryFile(path: string, content = 'content'): File {
  const parts = path.split('/')
  const file = new File([content], parts[parts.length - 1] ?? 'file.txt')
  Object.defineProperty(file, 'webkitRelativePath', { value: path })
  return file
}

describe('directory import planning', () => {
  it('removes the common top directory and classifies ignored and unsupported files', () => {
    const planned = planDirectoryImport([
      directoryFile('project/backend/app.py'),
      directoryFile('project/.git/config'),
      directoryFile('project/assets/logo.png'),
    ])

    expect(planned.map(({ relativePath }) => relativePath)).toEqual([
      'backend/app.py',
      '.git/config',
      'assets/logo.png',
    ])
    expect(planned.map(({ disposition }) => disposition)).toEqual([
      'ready',
      'ignored',
      'unsupported',
    ])
  })

  it('uses a maximum import concurrency of three', () => {
    expect(DIRECTORY_IMPORT_CONCURRENCY).toBe(3)
  })
})
