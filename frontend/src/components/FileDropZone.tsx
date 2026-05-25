import { useCallback, useRef, useState } from 'react'

const ACCEPTED_TYPES = [
  '.pdf', '.docx', '.pptx', '.txt', '.md',
  '.png', '.jpg', '.jpeg', '.tiff', '.tif',
  '.xlsx', '.csv',
]

const FILE_TYPE_LABELS = [
  { ext: 'PDF', icon: '📄' },
  { ext: 'DOCX', icon: '📝' },
  { ext: 'PPTX', icon: '📊' },
  { ext: 'TXT/MD', icon: '🗒️' },
  { ext: 'PNG/JPG/TIFF', icon: '🖼️' },
  { ext: 'XLSX/CSV', icon: '📈' },
]

interface Props {
  onFileSelected: (file: File) => void
  disabled?: boolean
}

export function FileDropZone({ onFileSelected, disabled }: Props) {
  const [isDragging, setIsDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(
    (file: File) => {
      if (!disabled) onFileSelected(file)
    },
    [disabled, onFileSelected]
  )

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    if (!disabled) setIsDragging(true)
  }

  const onDragLeave = () => setIsDragging(false)

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    e.target.value = ''
  }

  return (
    <div
      className={`
        relative rounded-xl border-2 border-dashed p-10 text-center transition-all duration-200 cursor-pointer
        ${isDragging
          ? 'border-blue-400 bg-blue-950/30 scale-[1.01]'
          : 'border-slate-600 hover:border-slate-400 bg-slate-800/40 hover:bg-slate-800/60'
        }
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
      `}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onClick={() => !disabled && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TYPES.join(',')}
        className="hidden"
        onChange={onInputChange}
        disabled={disabled}
      />

      <div className="flex flex-col items-center gap-3 pointer-events-none">
        <div className={`
          w-16 h-16 rounded-2xl flex items-center justify-center text-3xl transition-all
          ${isDragging ? 'bg-blue-600/30 scale-110' : 'bg-slate-700/60'}
        `}>
          {isDragging ? '📂' : '⬆️'}
        </div>

        <div>
          <p className="text-white font-semibold text-lg">
            {isDragging ? 'Drop it here' : 'Drag & drop your file'}
          </p>
          <p className="text-slate-400 text-sm mt-1">
            or <span className="text-blue-400 underline underline-offset-2">click to browse</span>
          </p>
        </div>

        <p className="text-slate-500 text-xs mt-1">Max 50 MB</p>

        <div className="flex flex-wrap justify-center gap-2 mt-2">
          {FILE_TYPE_LABELS.map(({ ext, icon }) => (
            <span
              key={ext}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-700/60 text-slate-300 text-xs border border-slate-600/50"
            >
              {icon} {ext}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
