import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  useAddBookmark,
  useRemoveBookmark,
  useUpsertReview,
  useWorkflow,
  useWorkflowItem,
} from "../api/queries";
import SeverityBadge from "../components/SeverityBadge";
import StatusBadge from "../components/StatusBadge";
import MapViewer from "../components/MapViewer";

const REVIEW_STATUSES = ["new", "reviewed", "item_of_interest", "dismissed", "false_positive", "needs_follow_up"];

function StacViewer({ stacItemId, stacItem }: { stacItemId: string; stacItem: object }) {
  const [query, setQuery] = useState("");
  const lines = JSON.stringify(stacItem, null, 2).split("\n");
  const q = query.trim().toLowerCase();
  const filtered = q ? lines.filter((l) => l.toLowerCase().includes(q)) : lines;

  return (
    <section className="mt-6 bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      <details>
        <summary className="px-5 py-3 text-sm font-medium text-gray-300 cursor-pointer select-none hover:bg-gray-800/50">
          STAC item — {stacItemId}
        </summary>
        <div className="px-4 py-2 border-t border-gray-800 bg-gray-900">
          <input
            type="text"
            placeholder="Search keys or values…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 placeholder-gray-500 focus:outline-none focus:border-brand-500"
          />
          {q && (
            <p className="text-xs text-gray-500 mt-1">{filtered.length} line{filtered.length !== 1 ? "s" : ""} matched</p>
          )}
        </div>
        <pre className="px-5 py-4 text-xs text-gray-300 overflow-auto max-h-[32rem] bg-gray-950 leading-relaxed">
          {filtered.map((line, i) => {
            if (!q) return line + "\n";
            const idx = line.toLowerCase().indexOf(q);
            return (
              <span key={i}>
                {line.slice(0, idx)}
                <mark className="bg-yellow-500/30 text-yellow-200 rounded-sm">{line.slice(idx, idx + q.length)}</mark>
                {line.slice(idx + q.length)}
                {"\n"}
              </span>
            );
          })}
        </pre>
      </details>
    </section>
  );
}

function ScoreBar({ value, severity }: { value: number; severity: string }) {
  const pct = Math.max(0, Math.min(100, ((value + 1) / 2) * 100));
  const colors: Record<string, string> = {
    green: "bg-green-500",
    yellow: "bg-yellow-500",
    red: "bg-red-500",
  };
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-gray-800 rounded-full h-2 overflow-hidden">
        <div
          className={`h-full rounded-full ${colors[severity] ?? "bg-gray-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-400 w-16 text-right">{value.toFixed(4)}</span>
    </div>
  );
}

export default function ItemDetailPage() {
  const { wfId, itemId } = useParams<{ wfId: string; itemId: string }>();
  const { data: item, isLoading } = useWorkflowItem(wfId!, itemId!);
  const { data: wf } = useWorkflow(wfId!);
  const addBookmark = useAddBookmark(wfId!, itemId!);
  const removeBookmark = useRemoveBookmark(wfId!, itemId!);
  const upsertReview = useUpsertReview(wfId!, itemId!);

  const [reviewStatus, setReviewStatus] = useState("");
  const [notes, setNotes] = useState("");
  const [reviewSubmitted, setReviewSubmitted] = useState(false);

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>;
  if (!item) return <div className="p-6 text-red-400">Item not found.</div>;

  const currentReview = item.review;

  async function submitReview() {
    await upsertReview.mutateAsync({
      review_status: reviewStatus || currentReview?.review_status || "new",
      notes: notes || currentReview?.notes || undefined,
    });
    setReviewSubmitted(true);
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Breadcrumb */}
      <div className="text-xs text-gray-400 mb-4 flex items-center gap-1">
        <Link to="/workflows" className="hover:text-gray-200">Workflows</Link>
        <span>/</span>
        <Link to={`/workflows/${wfId}`} className="hover:text-gray-200">Detail</Link>
        <span>/</span>
        <span className="text-gray-300">Item</span>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold mb-1 flex items-center gap-2">
            Scene {new Date(item.scene_datetime).toLocaleDateString()}
            <SeverityBadge severity={item.overall_severity} status={item.status} />
            <StatusBadge status={item.status} />
          </h1>
          <div className="text-sm text-gray-400">
            {item.collection_slug}
          </div>
        </div>
        <button
          onClick={() =>
            item.is_bookmarked
              ? removeBookmark.mutate()
              : addBookmark.mutate()
          }
          disabled={addBookmark.isPending || removeBookmark.isPending}
          className={`text-sm px-3 py-1.5 rounded border transition-colors ${item.is_bookmarked
              ? "border-yellow-600 text-yellow-400 bg-yellow-950/30 hover:bg-yellow-950/60"
              : "border-gray-700 text-gray-400 hover:border-gray-500"
            }`}
        >
          {item.is_bookmarked ? "★ Bookmarked" : "☆ Bookmark"}
        </button>
      </div>

      {/* Map */}
      {(wf?.aoi_geometry || item.stac_item.bbox) && (
        <section className="mb-6">
          <MapViewer
            aoi={wf?.aoi_geometry}
            items={
              item.stac_item.bbox
                ? [{
                  id: item.id,
                  bbox: item.stac_item.bbox as [number, number, number, number],
                  severity: item.overall_severity,
                  status: item.status,
                }]
                : []
            }
            className="h-56 w-full rounded-lg overflow-hidden border border-gray-800"
          />
        </section>
      )}

      {/* Model runs */}
      <section className="mb-6">
        <h2 className="text-sm font-medium text-gray-300 mb-3">Model runs</h2>
        {item.model_runs.length === 0 && (
          <p className="text-gray-500 text-sm">No model runs yet.</p>
        )}
        <div className="space-y-4">
          {item.model_runs.map((run) => (
            <div key={run.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-3">
                <span className="font-medium text-sm">{run.model_slug}</span>
                <StatusBadge status={run.status} />
                {run.completed_at && (
                  <span className="text-xs text-gray-500 ml-auto">
                    {new Date(run.completed_at).toLocaleString()}
                  </span>
                )}
              </div>
              {run.error_message && (
                <div className="mb-2 text-xs text-red-300 bg-red-950/40 border border-red-800 rounded px-3 py-2">
                  {run.error_message}
                </div>
              )}
              {run.scores.length === 0 && !run.error_message && (
                <p className="text-gray-500 text-xs">No scores.</p>
              )}
              <div className="space-y-3">
                {run.scores.map((score) => (
                  <div key={score.score_name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-gray-300 flex items-center gap-1.5">
                        {score.score_name}
                        {score.is_primary && (
                          <span className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">
                            primary
                          </span>
                        )}
                      </span>
                      <SeverityBadge severity={score.severity} />
                    </div>
                    <ScoreBar value={score.score_value} severity={score.severity} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Review */}
      <section className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h2 className="text-sm font-medium text-gray-300 mb-4">Review</h2>

        {currentReview && !reviewSubmitted && (
          <div className="mb-4 p-3 bg-gray-800 rounded text-sm text-gray-300">
            <span className="font-medium">Current:</span>{" "}
            <span className="capitalize">{currentReview.review_status.replace("_", " ")}</span>
            {currentReview.notes && <p className="text-gray-400 text-xs mt-1">{currentReview.notes}</p>}
          </div>
        )}

        {reviewSubmitted && (
          <p className="text-green-400 text-sm mb-3">Review saved.</p>
        )}

        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Status</label>
            <select
              value={reviewStatus || currentReview?.review_status || ""}
              onChange={(e) => setReviewStatus(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-brand-500"
            >
              <option value="">— Select —</option>
              {REVIEW_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mb-4">
          <label className="block text-xs text-gray-400 mb-1">Notes</label>
          <textarea
            rows={3}
            value={notes !== "" ? notes : currentReview?.notes ?? ""}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-brand-500 resize-none"
            placeholder="Optional notes…"
          />
        </div>

        <button
          onClick={submitReview}
          disabled={upsertReview.isPending}
          className="px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white rounded text-sm transition-colors"
        >
          {upsertReview.isPending ? "Saving…" : currentReview ? "Update review" : "Submit review"}
        </button>

        {upsertReview.error && (
          <p className="text-red-400 text-xs mt-2">{(upsertReview.error as Error).message}</p>
        )}
      </section>

      {/* STAC item JSON */}
      <StacViewer stacItemId={item.stac_item_id} stacItem={item.stac_item} />

      <div className="mt-4 text-xs text-gray-600">
        Discovered {new Date(item.discovered_at).toLocaleString()}
        {item.processed_at
          ? ` · Processed ${new Date(item.processed_at).toLocaleString()}`
          : ""}
      </div>
    </div>
  );
}
