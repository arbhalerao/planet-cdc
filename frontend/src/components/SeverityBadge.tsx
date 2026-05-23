const STYLES: Record<string, { cls: string; label: string }> = {
  green: { cls: "bg-green-900 text-green-300 border-green-700", label: "Normal" },
  yellow: { cls: "bg-yellow-900 text-yellow-300 border-yellow-700", label: "Caution" },
  red: { cls: "bg-red-900 text-red-300 border-red-700", label: "Alert" },
};

export default function SeverityBadge({ severity, status }: { severity: string | null; status?: string }) {
  if (!severity) {
    const label = status === "processed" ? "no data" : "—";
    return <span className="text-gray-500 text-xs">{label}</span>;
  }
  const { cls, label } = STYLES[severity] ?? { cls: "bg-gray-800 text-gray-400 border-gray-600", label: severity };
  return (
    <span className={`inline-block px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>
      {label}
    </span>
  );
}
