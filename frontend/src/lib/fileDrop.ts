// 서류 업로드 공용 헬퍼: 문서 확장자 필터 + 폴더 드롭(재귀) 수집

export const ALLOWED_EXT = ['.pdf', '.pptx', '.ppt', '.xlsx', '.xls']
export const isDocFile = (name: string) => ALLOWED_EXT.some(ext => name.toLowerCase().endsWith(ext))

// 드롭한 항목이 폴더면 내부 파일까지 재귀적으로 수집 (webkitGetAsEntry)
export async function collectFromDataTransfer(dt: DataTransfer): Promise<File[]> {
  const items = dt.items
  const hasEntry = items && items.length && (items[0] as any).webkitGetAsEntry
  if (!hasEntry) return Array.from(dt.files)

  const entries: any[] = []
  for (let i = 0; i < items.length; i++) {
    const e = (items[i] as any).webkitGetAsEntry?.()
    if (e) entries.push(e)
  }
  const out: File[] = []
  const walk = async (entry: any): Promise<void> => {
    if (entry.isFile) {
      await new Promise<void>(res => entry.file((f: File) => { out.push(f); res() }, () => res()))
    } else if (entry.isDirectory) {
      const reader = entry.createReader()
      const readBatch = (): Promise<any[]> => new Promise(res => reader.readEntries((es: any[]) => res(es), () => res([])))
      let batch = await readBatch()
      while (batch.length) {
        for (const sub of batch) await walk(sub)
        batch = await readBatch()
      }
    }
  }
  for (const e of entries) await walk(e)
  return out.length ? out : Array.from(dt.files)
}
