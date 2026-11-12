import { useEffect, useRef, useState } from 'react';
import { API_BASE_URL, type AnalysisReport, type Finding } from './analysis';

interface Generation {
  eligible: boolean;
  status: 'manual_review' | 'ineligible';
  generated_files: { path: string; content: string; requires_manual_review: boolean }[];
  existing_files: string[];
  skipped_files: { path: string; reason: string }[];
  generation_warnings: string[];
  unresolved_findings: Finding[];
  deployment_instructions: string[];
  download_zip_base64: string | null;
}

export default function GenerationFiles({ report, file }: { report: AnalysisReport; file: File }) {
  const eligible = report.project_type === 'web_application' && report.analysis_root !== null
    && /^[A-Za-z0-9_./-]+$/.test(report.analysis_root)
    && report.entry_point !== null && /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$/.test(report.entry_point)
    && ['Flask', 'FastAPI'].includes(report.framework);
  const [result, setResult] = useState<Generation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function generate() {
    setBusy(true); setError(''); setResult(null);
    const abort = new AbortController();
    controller.current = abort;
    const timeout = setTimeout(() => abort.abort(), 60000);
    try {
      const body = new FormData(); body.append('file', file);
      const response = await fetch(`${API_BASE_URL}/api/generate`, { method: 'POST', body, signal: abort.signal });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : `Generation failed (HTTP ${response.status}).`);
      }
      const data = await response.json();
      if (!data || !Array.isArray(data.generated_files) || !data.generated_files.every((item: unknown) => item && typeof item === 'object' && 'path' in item && typeof item.path === 'string' && 'content' in item && typeof item.content === 'string')
        || !Array.isArray(data.generation_warnings) || !Array.isArray(data.existing_files) || !Array.isArray(data.skipped_files)
        || !Array.isArray(data.deployment_instructions) || !(data.download_zip_base64 === null || typeof data.download_zip_base64 === 'string')) {
        throw new Error('Invalid generation response.');
      }
      if (typeof data.eligible !== 'boolean' || !['manual_review', 'ineligible'].includes(data.status)
        || ![data.generation_warnings, data.existing_files, data.deployment_instructions].every((items) => items.every((item: unknown) => typeof item === 'string'))
        || !data.skipped_files.every((item: { path?: unknown; reason?: unknown }) => item && typeof item.path === 'string' && typeof item.reason === 'string')) {
        throw new Error('Invalid generation response.');
      }
      setResult(data);
    } catch (failure) {
      setError(abort.signal.aborted ? 'Generation timed out or was cancelled.' : failure instanceof Error ? failure.message : 'Generation failed.');
    } finally { clearTimeout(timeout); setBusy(false); }
  }

  async function copy(path: string, content: string) {
    try { await navigator.clipboard.writeText(content); setCopied(path); }
    catch { setError('Copy failed. Select and copy the preview text manually.'); }
  }

  function download() {
    try {
      if (!result?.download_zip_base64) return;
      const bytes = Uint8Array.from(atob(result.download_zip_base64), (character) => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }));
      const link = document.createElement('a'); link.href = url; link.download = 'ascend-migration.zip';
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch { setError('Download failed. Copy individual generated files instead.'); }
  }

  return <section aria-label="Migration file generation" className="mt-6 border-t border-slate-200 pt-6">
    <h4 className="text-xl font-semibold">Migration files</h4>
    <p className="mt-2 text-sm">{eligible ? 'Eligible for review templates. Dependencies and runtime requirements will be checked during generation.' : 'Generation unavailable: select one supported application with a clear startup target.'}</p>
    <button disabled={!eligible || busy} onClick={() => void generate()} className="mt-4 rounded-lg bg-emerald-900 px-4 py-2 text-white disabled:opacity-50">{busy ? 'Generating…' : 'Generate Migration Files'}</button>
    {busy && <p role="status">Generating migration files…</p>}
    {error && <p role="alert" className="mt-3 text-red-700">{error}</p>}
    {result && <div className="mt-4 space-y-4">
      <p className="font-medium">{result.eligible ? 'Manual review required — files have not been built or deployed.' : 'Generation is not eligible.'}</p>
      {result.generation_warnings.map((warning) => <p key={warning} className="text-sm text-amber-900">{warning}</p>)}
      {result.existing_files.length > 0 && <p className="text-sm">Existing files preserved: {result.existing_files.join(', ')}</p>}
      {result.skipped_files.map((item) => <p key={item.path} className="text-sm">Skipped {item.path}: {item.reason}</p>)}
      {result.generated_files.map((item) => <details key={item.path} open className="rounded-lg border border-slate-200 p-3">
        <summary>{item.path} — review required</summary>
        <button onClick={() => void copy(item.path, item.content)} className="my-2 rounded border px-3 py-1">Copy {item.path}</button>
        {copied === item.path && <span role="status">Copied {item.path}</span>}
        <pre className="overflow-x-auto whitespace-pre-wrap text-xs"><code>{item.content}</code></pre>
      </details>)}
      {result.download_zip_base64 && <button onClick={download} className="rounded border border-emerald-900 px-4 py-2">Download migration ZIP</button>}
      <ol className="list-inside list-decimal text-sm">{result.deployment_instructions.map((step) => <li key={step}>{step}</li>)}</ol>
    </div>}
  </section>;
}
