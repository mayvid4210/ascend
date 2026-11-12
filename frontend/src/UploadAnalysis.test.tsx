import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import UploadAnalysis from './UploadAnalysis';
import { API_BASE_URL, type AnalysisReport } from './analysis';

const report: AnalysisReport = {
  readiness_score: 90, readiness_status: 'ready_for_migration_review', assessment_provisional: false,
  findings: [], severity_summary: { critical: 0, warning: 0, informational: 0 },
  migration_recommendations: ['Review deployment configuration.'], assessment_limitations: [],
  project_name: 'flask-app', framework: 'Flask', python_version: '3.13',
  project_type: 'web_application', analysis_root: '.', project_root_candidates: ['.'],
  dependency_files: ['requirements.txt'], excluded_application_objects: [],
  dependencies: [{ name: 'Flask', version: '==3.1.2' }], dependency_file_present: true,
  entry_point: 'app:app', entry_point_candidates: ['app:app'],
  entry_point_type: 'application_object', entry_point_candidate_types: { 'app:app': 'application_object' },
  environment_variables: ['API_KEY'], detected_ports: [5000], detected_hosts: ['127.0.0.1'],
  dockerfile_present: true, dockerignore_present: false, env_example_present: true,
  deployment_files: ['Dockerfile'], files_scanned: 3, analysis_warnings: ['Example warning'],
};

function select(file = new File(['zip data'], 'sample.zip', { type: 'application/zip' })) {
  fireEvent.change(screen.getByLabelText('Project ZIP (up to 10 MB)'), { target: { files: [file] } });
  return file;
}

function submit() {
  fireEvent.click(screen.getByRole('button', { name: 'Analyze project' }));
}

describe('project ZIP analysis', () => {
  it('requires selection and accepts ZIP files', () => {
    render(<UploadAnalysis />);
    expect(screen.getByRole('button')).toBeDisabled();
    select();
    expect(screen.getByRole('button')).toBeEnabled();
  });

  it('rejects non-ZIP files before requesting analysis', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<UploadAnalysis />);
    select(new File(['python'], 'main.py'));
    expect(screen.getByRole('alert')).toHaveTextContent('Select a ZIP archive');
    expect(screen.getByRole('button')).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects ZIP files over 10 MB', () => {
    render(<UploadAnalysis />);
    const file = new File(['small content'], 'large.zip');
    Object.defineProperty(file, 'size', { value: 10 * 1024 * 1024 + 1 });
    select(file);
    expect(screen.getByRole('alert')).toHaveTextContent('10 MB or smaller');
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('shows loading and disables controls during analysis', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const view = render(<UploadAnalysis />);
    select();
    submit();
    expect(screen.getByRole('status')).toHaveTextContent('Analyzing your project');
    expect(screen.getByRole('button')).toBeDisabled();
    expect(screen.getByLabelText('Project ZIP (up to 10 MB)')).toBeDisabled();
    view.unmount();
  });

  it('submits multipart data and renders the actual report', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => report });
    vi.stubGlobal('fetch', fetchMock);
    render(<UploadAnalysis />);
    const file = select();
    submit();
    const results = await screen.findByRole('region', { name: 'Analysis results' });
    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE_URL}/api/analyze`, expect.objectContaining({ method: 'POST' }));
    expect(fetchMock.mock.calls[0][1].body.get('file')).toBe(file);
    for (const text of ['Flask', '==3.1.2', 'app:app', 'API_KEY', '5000', '127.0.0.1', 'Dockerfile', 'Example warning']) {
      expect(results).toHaveTextContent(text);
    }
    expect(within(results).getByText('Analysis results: flask-app')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it.each([
    ['API error', () => Promise.resolve({ ok: false, status: 400, json: async () => ({ detail: 'Archive contains an unsafe path.' }) }), 'Archive contains an unsafe path.'],
    ['non-JSON API error', () => Promise.resolve({ ok: false, status: 413, json: async () => { throw new Error('plain text'); } }), 'Analysis failed (HTTP 413).'],
    ['network error', () => Promise.reject(new TypeError('Failed to fetch')), 'Could not reach the backend'],
    ['invalid response', () => Promise.resolve({ ok: true, json: async () => ({}) }), 'invalid analysis response'],
  ])('handles %s', async (_name, implementation, message) => {
    vi.stubGlobal('fetch', vi.fn(implementation));
    render(<UploadAnalysis />);
    select();
    submit();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(message));
    expect(screen.getByRole('button')).toBeEnabled();
    expect(screen.queryByRole('region', { name: 'Analysis results' })).not.toBeInTheDocument();
  });

  it('renders unknown values and empty lists without inventing details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, framework: 'Unknown', python_version: null, dependencies: [],
      dependency_file_present: false, entry_point: null, entry_point_candidates: [],
      entry_point_type: null, entry_point_candidate_types: {},
      environment_variables: [], detected_ports: [], detected_hosts: [],
      deployment_files: [], dockerfile_present: false, dockerignore_present: false,
      analysis_warnings: [],
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Analysis results' });
    expect(results).toHaveTextContent('Unknown or ambiguous');
    expect(results).toHaveTextContent('No supported dependency declaration found');
    expect(results).toHaveTextContent('None parsed');
    expect(results).not.toHaveTextContent('app:app');
  });

  it('clears stale results when another file is selected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => report }));
    render(<UploadAnalysis />);
    select();
    submit();
    await screen.findByRole('region', { name: 'Analysis results' });
    select(new File(['next'], 'next.zip'));
    expect(screen.queryByRole('region', { name: 'Analysis results' })).not.toBeInTheDocument();
  });

  it.each([
    ['python_library', 'Python library'],
    ['framework_repository', 'Framework source repository'],
    ['ambiguous', 'Ambiguous project'],
    ['unsupported', 'Unsupported or unknown project'],
  ])('shows %s classification and application-directory guidance', async (type, label) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, project_type: type, entry_point: null, entry_point_candidates: [],
      readiness_score: null, readiness_status: 'unavailable', assessment_provisional: true,
      entry_point_type: null, entry_point_candidate_types: {},
      analysis_root: type === 'ambiguous' ? null : '.',
      project_root_candidates: type === 'ambiguous' ? ['one', 'two'] : ['.'],
      analysis_warnings: ['Upload a specific application directory instead.'],
      excluded_application_objects: [{ path: 'tests/app.py', object_name: 'app', scope: 'test' }],
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Analysis results' });
    expect(results).toHaveTextContent(label);
    expect(results).toHaveTextContent('Upload a specific application directory instead.');
    expect(results).toHaveTextContent('Excluded non-production application objects (1)');
    if (type === 'ambiguous') {
      expect(results).toHaveTextContent('Not selected — multiple project roots');
      expect(results).toHaveTextContent('one, two');
    }
  });

  it('shows pyproject.toml as the dependency source', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, dependency_files: ['pyproject.toml'],
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Analysis results' });
    expect(results).toHaveTextContent('Web application');
    expect(results).toHaveTextContent('pyproject.toml');
    expect(results).not.toHaveTextContent('requirements.txt');
  });

  it.each([
    ['application_object', 'main:app', 'Application object'],
    ['application_factory', 'flaskr:create_app', 'Application factory'],
  ])('renders the %s entry point type', async (type, target, label) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, entry_point: target, entry_point_type: type,
      entry_point_candidates: [target], entry_point_candidate_types: { [target]: type },
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Analysis results' });
    expect(within(results).getByText('Entry point type').nextElementSibling).toHaveTextContent(label);
    expect(results).toHaveTextContent(`${target} (${label})`);
  });

  it('keeps a mixed set of startup candidates ambiguous', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, project_type: 'ambiguous', entry_point: null, entry_point_type: null,
      entry_point_candidates: ['main:app', 'flaskr:create_app'],
      entry_point_candidate_types: { 'main:app': 'application_object', 'flaskr:create_app': 'application_factory' },
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Analysis results' });
    expect(within(results).getByText('Entry point type').nextElementSibling).toHaveTextContent('Unknown or ambiguous');
    expect(results).toHaveTextContent('main:app (Application object)');
    expect(results).toHaveTextContent('flaskr:create_app (Application factory)');
  });

  it('renders the backend readiness score and severity summary', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, readiness_score: 72, readiness_status: 'needs_improvements', assessment_provisional: true,
      severity_summary: { critical: 1, warning: 2, informational: 3 },
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Cloud readiness' });
    expect(results).toHaveTextContent('Score: 72/100');
    expect(results).toHaveTextContent('Needs improvements');
    expect(results).toHaveTextContent('Critical: 1 · Warning: 2 · Informational: 3');
    expect(results).toHaveTextContent('Provisional assessment');
    expect(results).toHaveTextContent('Static analysis does not guarantee successful deployment.');
  });

  it('renders finding confidence, location, explanation, fix, and checklist from the response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, findings: [{ rule_id: 'NET001', title: 'Loopback-only server binding', severity: 'warning',
        confidence: 'potential', category: 'networking', file_path: 'main.py', line_number: 12,
        explanation: 'The production command may override a development startup call.',
        recommended_fix: 'Configure the production interface.' }],
      migration_recommendations: ['Configure the production interface.', 'Review the PaaS configuration.'],
      assessment_limitations: ['External services were not checked.'],
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Cloud readiness' });
    expect(results).toHaveTextContent('Loopback-only server binding');
    expect(results).toHaveTextContent('NET001 · warning · potential · networking');
    expect(results).toHaveTextContent('main.py:12');
    expect(results).toHaveTextContent('The production command may override');
    expect(results).toHaveTextContent('Recommended fix: Configure the production interface.');
    expect(results).toHaveTextContent('Prioritized migration checklist');
    expect(results).toHaveTextContent('Review the PaaS configuration.');
    expect(results).toHaveTextContent('External services were not checked.');
  });

  it('shows unavailable assessments without inventing a numeric score', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...report, project_type: 'python_library', readiness_score: null, readiness_status: 'unavailable',
      assessment_provisional: true, assessment_limitations: ['Upload one supported web application directory.'],
    }) }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Cloud readiness' });
    expect(results).toHaveTextContent('Score unavailable');
    expect(results).toHaveTextContent('Assessment unavailable');
    expect(results).not.toHaveTextContent('/100');
    expect(results).toHaveTextContent('Upload one supported web application directory.');
  });

  it('does not mark a nonprovisional report as provisional', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => report }));
    render(<UploadAnalysis />);
    select();
    submit();
    const results = await screen.findByRole('region', { name: 'Cloud readiness' });
    expect(results).toHaveTextContent('Score: 90/100');
    expect(results).not.toHaveTextContent('Provisional assessment');
  });

  it('times out and allows another attempt', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url, { signal }: RequestInit) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('Timeout', 'AbortError')));
    })));
    render(<UploadAnalysis />);
    select();
    submit();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(screen.getByRole('alert')).toHaveTextContent('Analysis timed out');
    expect(screen.getByRole('button')).toBeEnabled();
  });
});
