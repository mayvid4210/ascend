import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import GenerationFiles from './GenerationFiles';
import { API_BASE_URL, type AnalysisReport } from './analysis';

const application = {
  project_type: 'web_application', framework: 'FastAPI', analysis_root: '.', entry_point: 'main:app',
} as AnalysisReport;
const file = new File(['zip-data'], 'project.zip', { type: 'application/zip' });
const generated = {
  eligible: true, status: 'manual_review',
  generated_files: [{ path: 'Dockerfile', content: 'FROM python:3.13-slim\n', requires_manual_review: true }],
  existing_files: ['.env.example'], skipped_files: [{ path: '.env.example', reason: 'Existing file preserved.' }],
  generation_warnings: ['Specialized runtime packages require manual review.'], unresolved_findings: [],
  deployment_instructions: ['Build locally after reviewing configuration.'], download_zip_base64: btoa('zip-bytes'),
};

function start() { fireEvent.click(screen.getByRole('button', { name: 'Generate Migration Files' })); }

describe('migration file generation', () => {
  it.each(['python_library', 'framework_repository', 'ambiguous', 'unsupported'])('disables %s projects', (project_type) => {
    render(<GenerationFiles report={{ ...application, project_type } as AnalysisReport} file={file} />);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(screen.getByText(/Generation unavailable/)).toBeInTheDocument();
  });

  it('disables ambiguous startup targets', () => {
    render(<GenerationFiles report={{ ...application, entry_point: null }} file={file} />);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('disables unsafe application root names', () => {
    render(<GenerationFiles report={{ ...application, analysis_root: 'api$(unsafe)' }} file={file} />);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('uploads the ZIP again and shows previews, existing files, and manual review warnings', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => generated });
    vi.stubGlobal('fetch', fetchMock);
    render(<GenerationFiles report={application} file={file} />);
    start();
    expect(await screen.findByText('FROM python:3.13-slim')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE_URL}/api/generate`, expect.objectContaining({ method: 'POST' }));
    expect(fetchMock.mock.calls[0][1].body.get('file')).toBe(file);
    expect(screen.getByText(/Existing files preserved: .env.example/)).toBeInTheDocument();
    expect(screen.getByText(/Skipped .env.example/)).toBeInTheDocument();
    expect(screen.getByText('Specialized runtime packages require manual review.')).toBeInTheDocument();
    expect(screen.getByText(/files have not been built or deployed/)).toBeInTheDocument();
  });

  it('shows loading and prevents duplicate requests', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<GenerationFiles report={application} file={file} />);
    start();
    expect(screen.getByRole('status')).toHaveTextContent('Generating migration files');
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it.each([
    [false, { detail: 'Malformed ZIP archive.' }, 'Malformed ZIP archive.'],
    [true, {}, 'Invalid generation response.'],
  ])('handles unsuccessful or invalid API responses', async (ok, payload, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok, status: 400, json: async () => payload }));
    render(<GenerationFiles report={application} file={file} />);
    start();
    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(screen.getByRole('button', { name: 'Generate Migration Files' })).toBeEnabled();
  });

  it('handles network errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network unavailable')));
    render(<GenerationFiles report={application} file={file} />);
    start();
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable');
  });

  it('copies generated content', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => generated }));
    render(<GenerationFiles report={application} file={file} />);
    start();
    fireEvent.click(await screen.findByRole('button', { name: 'Copy Dockerfile' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('FROM python:3.13-slim\n'));
    expect(await screen.findByRole('status')).toHaveTextContent('Copied Dockerfile');
  });

  it('downloads a ZIP blob and releases the object URL', async () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:migration');
    const revokeObjectURL = vi.fn();
    const OriginalURL = URL;
    class DownloadURL extends OriginalURL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal('URL', DownloadURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => generated }));
    render(<GenerationFiles report={application} file={file} />);
    start();
    fireEvent.click(await screen.findByRole('button', { name: 'Download migration ZIP' }));
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledOnce();
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:migration'));
    click.mockRestore();
  });

  it('times out a stalled generation request', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url, { signal }: RequestInit) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new Error('aborted')));
    })));
    render(<GenerationFiles report={application} file={file} />);
    start();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(screen.getByRole('alert')).toHaveTextContent('Generation timed out');
  });
});
