import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { validateUploadFile } from './api';
import type { UploadedDocument } from './api';

export type UploadOutcome =
  | { kind: 'confirmed'; document: UploadedDocument }
  | { kind: 'unconfirmed' | 'rejected'; message: string };

type Props = {
  workspaceName: string;
  knowledgeBaseName: string;
  activity: 'idle' | 'uploading' | 'blocked';
  outcome: UploadOutcome | null;
  onUpload: (file: File) => void;
  onCancel: () => void;
  canContinue?: boolean;
  onContinue?: () => void;
};

function fileSize(bytes: number): string {
  return bytes < 1024 ? `${bytes} 字节` : `${(bytes / 1024).toFixed(1)} KiB`;
}

export default function DocumentUpload({ workspaceName, knowledgeBaseName, activity,
  outcome, onUpload, onCancel, canContinue = false, onContinue }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [uploadingFilename, setUploadingFilename] = useState('');
  const busy = activity !== 'idle';

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !selectedFile) return;
    const error = validateUploadFile(selectedFile);
    setValidationError(error);
    if (error) return;
    const file = selectedFile;
    setUploadingFilename(file.name);
    setSelectedFile(null);
    if (inputRef.current) inputRef.current.value = '';
    onUpload(file);
  }

  return <section className="upload-panel" aria-labelledby="upload-title">
    <h2 id="upload-title">上传文档</h2>
    <p className="upload-note">目标工作空间：<strong>{workspaceName}</strong>；
      目标知识库：<strong>{knowledgeBaseName}</strong>。</p>
    <form className="upload-form" onSubmit={submit}>
      <label htmlFor="upload-file">选择文档</label>
      <input id="upload-file" ref={inputRef} type="file" accept=".txt,.md,.pdf"
        aria-label="选择文档" disabled={busy}
        aria-describedby="upload-help" onChange={event => {
          const file = event.currentTarget.files?.[0] ?? null;
          const error = file ? validateUploadFile(file) : null;
          setValidationError(error);
          setSelectedFile(error ? null : file);
        }} />
      <p className="upload-note" id="upload-help">支持 TXT、Markdown 和 PDF，单份文件不超过 5 MiB。
        选中文件后，点击“上传”才会发送文件。</p>
      {selectedFile && <p className="file-summary">已选择：<strong>{selectedFile.name}</strong>
        {' '}（{fileSize(selectedFile.size)}）</p>}
      {validationError && <p className="message error" role="alert">{validationError}</p>}
      <div className="upload-actions">
        {selectedFile && <button className="button secondary" type="button"
          disabled={busy} onClick={() => {
            setSelectedFile(null);
            setValidationError(null);
            if (inputRef.current) inputRef.current.value = '';
          }}>取消选择</button>}
        <button className="button primary" type="submit" disabled={busy || !selectedFile}>上传</button>
        {activity === 'uploading' && <button className="button secondary" type="button"
          onClick={onCancel}>停止等待上传结果</button>}
      </div>
    </form>
    {activity === 'uploading' && <div>
      <p className="message" role="status">正在上传并解析…{uploadingFilename && ` ${uploadingFilename}`}</p>
      <p className="upload-note">停止等待不保证后端未创建文档，请先核对文档列表再决定是否重新上传。</p>
    </div>}
    {outcome && <section className="upload-result" aria-labelledby="upload-result-title">
      <h3 id="upload-result-title">本次上传结果</h3>
      {outcome.kind === 'confirmed' ? <div role="status">
        <p>文档：<strong>{outcome.document.originalFilename}</strong></p>
        {outcome.document.status === 'parsed'
          ? <p><strong>已解析</strong>：文档记录已创建，文字解析完成；尚未可检索。</p>
          : <><p><strong>解析失败</strong>：文档记录已创建，原件解析失败。</p>
            {outcome.document.parseError && <p>{outcome.document.parseError}</p>}</>}
        <p className="upload-note">列表仅展示第一页，新记录未必出现在当前页；本卡片展示本次上传结果。</p>
        {outcome.document.status === 'parsed' && onContinue && <div className="upload-actions">
          <button className="button primary" type="button" disabled={!canContinue}
            onClick={() => { if (canContinue) onContinue(); }}>继续处理这份文档</button>
          <p className="upload-note">进入文档页后，可查看处理状态并手动切块。</p>
        </div>}
      </div> : <div>
        <p className="message error" role="alert">{outcome.message}</p>
        {outcome.kind === 'unconfirmed' && <p className="upload-note">
          请先核对知识库文档记录；列表只含第一页，未看到新记录不能证明上传失败。
          如决定再次上传，需要重新选择文件后再点击“上传”，再次上传可能创建重复记录。
        </p>}
      </div>}
    </section>}
  </section>;
}
