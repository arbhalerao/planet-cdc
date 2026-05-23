const COLORS: Record<string, string> = {
  draft: "bg-gray-800 text-gray-400",
  pending: "bg-gray-800 text-gray-400",
  running: "bg-blue-900 text-blue-300",
  completed: "bg-green-900 text-green-300",
  completed_with_errors: "bg-yellow-900 text-yellow-300",
  failed: "bg-red-900 text-red-300",
  queued: "bg-gray-800 text-gray-400",
  fetching: "bg-blue-900 text-blue-300",
  scoring: "bg-purple-900 text-purple-300",
  fetch_failed: "bg-red-900 text-red-300",
  score_failed: "bg-red-900 text-red-300",
  processed: "bg-green-900 text-green-300",
  success: "bg-green-900 text-green-300",
};

const LABELS: Record<string, string> = {
  completed_with_errors: "partial",
  fetch_failed: "fetch failed",
  score_failed: "score failed",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium capitalize ${COLORS[status] ?? "bg-gray-800 text-gray-400"}`}
    >
      {LABELS[status] ?? status}
    </span>
  );
}
