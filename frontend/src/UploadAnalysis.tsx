import { useEffect, useRef, useState, type FormEvent } from 'react';
import { API_BASE_URL, MAX_UPLOAD_BYTES, isAnalysisReport, type AnalysisReport } from './analysis';
import ReadinessResults from './ReadinessResults';
import GenerationFiles from './GenerationFiles';

const projectTypes = {
  web_application: 'Web application', python_library: 'Python library',
  framework_repository: 'Framework source repository', ambiguous: 'Ambiguous project',
  unsupported: 'Unsupported or unknown project',
};
const entryPointTypes = { application_object: 'Application object', application_factory: 'Application factory' };

export default function UploadAnalysis() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const request = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => {
    request.current?.abort();
    clearTimeout(timer.current);
  }, []);

  function selectFile(selected: File | undefined) {
    setReport(null);
    setError('');
    setFile(null);
    if (!selected) return;
    if (!selected.name.toLowerCase().endsWith('.zip')) {
      setError('Select a ZIP archive (.zip).');
    } else if (selected.size > MAX_UPLOAD_BYTES) {
      setError('The ZIP archive must be 10 MB or smaller.');
    } else {
      setFile(selected);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || loading) return;
    setLoading(true);
    setReport(null);
    setError('');
    const controller = new AbortController();
    request.current = controller;
    const timeout = setTimeout(() => controller.abort(), 60000);
    timer.current = timeout;
    try {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(`${API_BASE_URL}/api/analyze`, {
        method: 'POST', body, signal: controller.signal,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : `Analysis failed (HTTP ${response.status}).`);
      }
      const result: unknown = await response.json();
      if (!isAnalysisReport(result)) {
        throw new Error('The backend returned an invalid analysis response.');
      }
      setReport(result);
    } catch (failure) {
      setError(controller.signal.aborted
        ? 'Analysis timed out. Try a smaller archive or check the backend.'
        : failure instanceof Error && failure.message !== 'Failed to fetch'
          ? failure.message : 'Could not reach the backend. Check that it is running and try again.');
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  }

  return (
    <section aria-labelledby="upload-heading" className="mt-8 w-full max-w-3xl rounded-2xl border border-emerald-950/15 bg-white p-6">
      <h2 id="upload-heading" className="text-2xl font-semibold">Analyze a Python application</h2>
      <p className="mt-2 text-sm text-slate-600">Upload a ZIP to inspect its framework, dependencies, and runtime configuration. Uploaded code is never executed.</p>
      <form onSubmit={submit} className="mt-5 space-y-4">
        <label className="block text-sm font-medium" htmlFor="project-zip">Project ZIP (up to 10 MB)</label>
        <input id="project-zip" type="file" accept=".zip,application/zip" disabled={loading}
          onChange={(event) => selectFile(event.target.files?.[0])}
          className="block w-full text-sm file:mr-4 file:rounded-lg file:border-0 file:bg-emerald-50 file:px-4 file:py-2 file:text-emerald-900" />
        <button type="submit" disabled={!file || loading}
          className="rounded-lg bg-emerald-900 px-5 py-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
          {loading ? 'Analyzing…' : 'Analyze project'}
        </button>
        {loading && <p role="status" className="text-sm text-slate-600">Analyzing your project…</p>}
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      </form>
      {report && <section aria-label="Analysis results" className="mt-8 border-t border-slate-200 pt-6">
        <h3 className="text-xl font-semibold">Analysis results: {report.project_name}</h3>
        <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[12rem_1fr]">
          <dt className="font-medium">Project type</dt><dd>{projectTypes[report.project_type]}</dd>
          <dt className="font-medium">Analysis root</dt><dd>{report.analysis_root ?? 'Not selected — multiple project roots'}</dd>
          <dt className="font-medium">Project root candidates</dt><dd>{report.project_root_candidates.join(', ') || 'None detected'}</dd>
          <dt className="font-medium">Framework</dt><dd>{report.framework}</dd>
          <dt className="font-medium">Python version</dt><dd>{report.python_version ?? 'Unknown'}</dd>
          <dt className="font-medium">Entry point</dt><dd>{report.entry_point ?? 'Unknown or ambiguous'}</dd>
          <dt className="font-medium">Entry point type</dt><dd>{report.entry_point_type ? entryPointTypes[report.entry_point_type] : 'Unknown or ambiguous'}</dd>
          <dt className="font-medium">Entry point candidates</dt><dd>{report.entry_point_candidates.map((target) =>
            `${target}${report.entry_point_candidate_types[target] ? ` (${entryPointTypes[report.entry_point_candidate_types[target]]})` : ''}`
          ).join(', ') || 'None detected'}</dd>
          <dt className="font-medium">Dependencies declared</dt><dd>{report.dependency_file_present ? report.dependency_files.join(', ') : 'No supported dependency declaration found'}</dd>
          <dt className="font-medium">Environment variables</dt><dd>{report.environment_variables.join(', ') || 'None detected'}</dd>
          <dt className="font-medium">Example environment file</dt><dd>{report.env_example_present ? 'Present' : 'Not detected'}</dd>
          <dt className="font-medium">Ports</dt><dd>{report.detected_ports.join(', ') || 'None detected'}</dd>
          <dt className="font-medium">Hosts</dt><dd>{report.detected_hosts.join(', ') || 'None detected'}</dd>
          <dt className="font-medium">Dockerfile</dt><dd>{report.dockerfile_present ? 'Present' : 'Not detected'}</dd>
          <dt className="font-medium">.dockerignore</dt><dd>{report.dockerignore_present ? 'Present' : 'Not detected'}</dd>
          <dt className="font-medium">Deployment files</dt><dd className="break-words">{report.deployment_files.join(', ') || 'None detected'}</dd>
          <dt className="font-medium">Files scanned</dt><dd>{report.files_scanned}</dd>
        </dl>
        <ReadinessResults report={report} />
        {file && <GenerationFiles report={report} file={file} />}
        {report.excluded_application_objects.length > 0 && <details className="mt-5 text-sm">
          <summary className="cursor-pointer font-medium">Excluded non-production application objects ({report.excluded_application_objects.length})</summary>
          <ul className="mt-2 list-inside list-disc">{report.excluded_application_objects.map((item) => <li key={`${item.path}:${item.object_name}`}>
            {item.path}:{item.object_name} ({item.scope})
          </li>)}</ul>
        </details>}
        <h4 className="mt-5 font-medium">Dependencies</h4>
        {report.dependencies.length ? <ul className="mt-2 list-inside list-disc text-sm">
          {report.dependencies.map((item, index) => <li key={`${item.name}-${index}`}>{item.name} {item.version ?? '(version unspecified)'}</li>)}
        </ul> : <p className="mt-2 text-sm">None parsed</p>}
        {report.analysis_warnings.length > 0 && <>
          <h4 className="mt-5 font-medium">Analysis warnings</h4>
          <ul className="mt-2 list-inside list-disc text-sm text-amber-900">{report.analysis_warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </>}
      </section>}
    </section>
  );
}
