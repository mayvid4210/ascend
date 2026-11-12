import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';

describe('backend connection status', () => {
  it('shows loading while the request is pending', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<App />);
    expect(screen.getByRole('heading', { name: /^Ascend$/ })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Checking backend connection…');
  });

  it('shows success and requests the health endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ name: 'Ascend', status: 'ok' }) });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    expect(await screen.findByText('Backend connected')).toBeInTheDocument();
    const base = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
    expect(fetchMock).toHaveBeenCalledWith(`${base}/api/health`, { signal: expect.any(AbortSignal) });
  });

  it.each([
    ['network failure', () => Promise.reject(new Error('offline'))],
    ['HTTP failure', () => Promise.resolve({ ok: false })],
    ['invalid payload', () => Promise.resolve({ ok: true, json: async () => ({ status: 'wrong' }) })],
    ['invalid JSON', () => Promise.resolve({ ok: true, json: async () => { throw new Error('invalid JSON'); } })],
  ])('shows unavailable after %s', async (_name, implementation) => {
    vi.stubGlobal('fetch', vi.fn(implementation));
    render(<App />);
    expect(await screen.findByText('Backend unavailable')).toBeInTheDocument();
  });

  it('shows unavailable when the backend times out', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url, { signal }: RequestInit) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new Error('aborted')));
    })));
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByRole('status')).toHaveTextContent('Backend unavailable');
  });
});
