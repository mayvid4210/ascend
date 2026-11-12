import type { AnalysisReport } from './analysis';

const statusLabels = {
  ready_for_migration_review: 'Ready for migration review', needs_improvements: 'Needs improvements',
  significant_migration_concerns: 'Significant migration concerns', unavailable: 'Assessment unavailable',
};

export default function ReadinessResults({ report }: { report: AnalysisReport }) {
  return <section aria-label="Cloud readiness" className="mt-6 border-t border-slate-200 pt-6">
    <h4 className="text-xl font-semibold">Cloud migration readiness</h4>
    <p className="mt-3 font-medium">{report.readiness_score === null ? 'Score unavailable' : `Score: ${report.readiness_score}/100`} · {statusLabels[report.readiness_status]}</p>
    {report.assessment_provisional && <p className="mt-2 text-sm text-amber-900">Provisional assessment — important information is unknown or potential concerns need review.</p>}
    <p className="mt-3 text-sm">Critical: {report.severity_summary.critical} · Warning: {report.severity_summary.warning} · Informational: {report.severity_summary.informational}</p>
    <p className="mt-3 text-sm text-slate-600">Static analysis does not guarantee successful deployment.</p>
    {report.findings.length > 0 && <ul className="mt-5 space-y-5">{report.findings.map((finding) => <li key={finding.rule_id} className="rounded-lg border border-slate-200 p-4">
      <p className="font-medium">{finding.title}</p>
      <p className="mt-1 text-xs uppercase tracking-wide text-slate-600">{finding.rule_id} · {finding.severity} · {finding.confidence} · {finding.category}</p>
      {finding.file_path && <p className="mt-2 break-words text-sm text-slate-600">{finding.file_path}{finding.line_number !== null ? `:${finding.line_number}` : ''}</p>}
      <p className="mt-2 text-sm">{finding.explanation}</p>
      <p className="mt-2 text-sm"><strong>Recommended fix: </strong>{finding.recommended_fix}</p>
    </li>)}</ul>}
    <h5 className="mt-6 font-medium">Prioritized migration checklist</h5>
    <ol className="mt-2 list-inside list-decimal space-y-2 text-sm">{report.migration_recommendations.map((recommendation) => <li key={recommendation}>{recommendation}</li>)}</ol>
    {report.assessment_limitations.length > 0 && <>
      <h5 className="mt-5 font-medium">Assessment limitations</h5>
      <ul className="mt-2 list-inside list-disc space-y-2 text-sm text-slate-600">{report.assessment_limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
    </>}
  </section>;
}
