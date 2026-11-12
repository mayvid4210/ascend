import { useEffect, useState } from 'react';
import UploadAnalysis from './UploadAnalysis';
import { API_BASE_URL } from './analysis';

const apiBaseUrl = API_BASE_URL;
type Status = 'loading' | 'success' | 'unavailable';

export default function App() {
  const [status, setStatus] = useState<Status>('loading');

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    let active = true;

    async function checkHealth() {
      try {
        const response = await fetch(`${apiBaseUrl}/api/health`, { signal: controller.signal });
        if (!response.ok) throw new Error('Health request failed');
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('name' in data) || data.name !== 'Ascend'
          || !('status' in data) || data.status !== 'ok') {
          throw new Error('Unexpected health response');
        }
        if (active) setStatus('success');
      } catch {
        if (active) setStatus('unavailable');
      } finally {
        clearTimeout(timeout);
      }
    }

    void checkHealth();
    return () => {
      active = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  const labels = {
    loading: 'Checking backend connection…',
    success: 'Backend connected',
    unavailable: 'Backend unavailable',
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 sm:px-12">
      <header className="flex items-center justify-between border-b border-emerald-950/15 py-7">
        <span className="text-2xl font-bold tracking-tight">↗ Ascend</span>
        <span className="text-xs font-medium uppercase tracking-widest text-emerald-900">Phase 02 / Application analysis</span>
      </header>
      <main className="flex flex-1 flex-col justify-center py-20">
        <p className="mb-6 text-sm font-medium uppercase tracking-[0.2em] text-emerald-800">A clearer path to the cloud</p>
        <h1 className="text-7xl font-semibold tracking-tighter sm:text-8xl">Ascend</h1>
        <h2 className="mt-6 max-w-xl text-3xl leading-tight tracking-tight sm:text-5xl">From localhost to the cloud</h2>
        <p className="mt-6 max-w-xl text-lg text-slate-600">Automated Application-to-Cloud Migration Toolkit</p>
        <section aria-label="Backend connection" className="mt-12 max-w-xl rounded-2xl border border-emerald-950/15 bg-white p-6">
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">Local development / API status</p>
          <p role="status" aria-live="polite" className="flex items-center gap-3 font-medium">
            <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${status === 'success' ? 'bg-emerald-600' : status === 'loading' ? 'bg-amber-500' : 'bg-red-500'}`} />
            {labels[status]}
          </p>
          {status === 'unavailable' && <p className="mt-3 text-sm text-slate-600">Start the FastAPI backend and refresh this page to reconnect.</p>}
        </section>
        <UploadAnalysis />
      </main>
      <footer className="border-t border-emerald-950/15 py-6 text-sm text-slate-500">Cloud Computing course project · Built to learn, built to simplify.</footer>
    </div>
  );
}
