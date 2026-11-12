export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export type EntryPointType = 'application_object' | 'application_factory';
export interface Finding {
  rule_id: string;
  title: string;
  severity: 'critical' | 'warning' | 'informational';
  confidence: 'confirmed' | 'potential';
  category: 'dependencies' | 'runtime' | 'networking' | 'storage' | 'security' | 'containerization';
  file_path: string | null;
  line_number: number | null;
  explanation: string;
  recommended_fix: string;
}

export interface AnalysisReport {
  readiness_score: number | null;
  readiness_status: 'ready_for_migration_review' | 'needs_improvements' | 'significant_migration_concerns' | 'unavailable';
  assessment_provisional: boolean;
  findings: Finding[];
  severity_summary: { critical: number; warning: number; informational: number };
  migration_recommendations: string[];
  assessment_limitations: string[];
  project_name: string;
  project_type: 'web_application' | 'python_library' | 'framework_repository' | 'ambiguous' | 'unsupported';
  analysis_root: string | null;
  project_root_candidates: string[];
  dependency_files: string[];
  excluded_application_objects: { path: string; object_name: string; scope: string }[];
  framework: 'Flask' | 'FastAPI' | 'Unknown';
  python_version: string | null;
  dependencies: { name: string; version: string | null }[];
  dependency_file_present: boolean;
  entry_point: string | null;
  entry_point_type: EntryPointType | null;
  entry_point_candidate_types: Record<string, EntryPointType>;
  entry_point_candidates: string[];
  environment_variables: string[];
  detected_ports: number[];
  detected_hosts: string[];
  dockerfile_present: boolean;
  dockerignore_present: boolean;
  env_example_present: boolean;
  deployment_files: string[];
  files_scanned: number;
  analysis_warnings: string[];
}

export function isAnalysisReport(value: unknown): value is AnalysisReport {
  if (!value || typeof value !== 'object') return false;
  const report = value as Record<string, unknown>;
  const strings = ['entry_point_candidates', 'environment_variables', 'detected_hosts', 'deployment_files', 'analysis_warnings', 'project_root_candidates', 'dependency_files', 'migration_recommendations', 'assessment_limitations'];
  return typeof report.project_name === 'string'
    && (report.readiness_score === null || typeof report.readiness_score === 'number' && Number.isInteger(report.readiness_score) && report.readiness_score >= 0 && report.readiness_score <= 100)
    && ['ready_for_migration_review', 'needs_improvements', 'significant_migration_concerns', 'unavailable'].includes(String(report.readiness_status))
    && typeof report.assessment_provisional === 'boolean'
    && !!report.severity_summary && typeof report.severity_summary === 'object'
    && ['critical', 'warning', 'informational'].every((severity) => typeof (report.severity_summary as Record<string, unknown>)[severity] === 'number')
    && Array.isArray(report.findings) && report.findings.every((item) => item
      && ['rule_id', 'title', 'explanation', 'recommended_fix'].every((key) => typeof item[key] === 'string')
      && ['critical', 'warning', 'informational'].includes(item.severity)
      && ['confirmed', 'potential'].includes(item.confidence)
      && ['dependencies', 'runtime', 'networking', 'storage', 'security', 'containerization'].includes(item.category)
      && (item.file_path === null || typeof item.file_path === 'string')
      && (item.line_number === null || Number.isInteger(item.line_number) && item.line_number > 0))
    && ['web_application', 'python_library', 'framework_repository', 'ambiguous', 'unsupported'].includes(String(report.project_type))
    && (report.analysis_root === null || typeof report.analysis_root === 'string')
    && Array.isArray(report.excluded_application_objects) && report.excluded_application_objects.every((item) => item
      && typeof item.path === 'string' && typeof item.object_name === 'string' && typeof item.scope === 'string')
    && ['Flask', 'FastAPI', 'Unknown'].includes(String(report.framework))
    && (report.python_version === null || typeof report.python_version === 'string')
    && (report.entry_point === null || typeof report.entry_point === 'string')
    && (report.entry_point_type === null || ['application_object', 'application_factory'].includes(String(report.entry_point_type)))
    && !!report.entry_point_candidate_types && typeof report.entry_point_candidate_types === 'object'
    && !Array.isArray(report.entry_point_candidate_types)
    && Object.values(report.entry_point_candidate_types).every((type) => ['application_object', 'application_factory'].includes(String(type)))
    && ['dependency_file_present', 'dockerfile_present', 'dockerignore_present', 'env_example_present'].every((key) => typeof report[key] === 'boolean')
    && typeof report.files_scanned === 'number'
    && strings.every((key) => Array.isArray(report[key]) && report[key].every((item: unknown) => typeof item === 'string'))
    && Array.isArray(report.detected_ports) && report.detected_ports.every((port) => typeof port === 'number')
    && Array.isArray(report.dependencies) && report.dependencies.every((item) => item && typeof item.name === 'string'
      && (item.version === null || typeof item.version === 'string'));
}
