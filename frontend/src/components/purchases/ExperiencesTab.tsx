import { useEffect, useId, useState } from 'react';
import type { ExperienceEntry } from '../../types';
import {
  useCreateExperience,
  useDeleteExperience,
  useExperiences,
  useUpdateExperience,
} from '../../hooks/useExperiences';

const CATEGORY_STYLES: Record<string, { label: string; cls: string }> = {
  timing: { label: '时机', cls: 'bg-amber-50 text-amber-700 border-amber-200' },
  route: { label: '航线', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  holiday: { label: '节假日', cls: 'bg-purple-50 text-purple-700 border-purple-200' },
  general: { label: '通用', cls: 'bg-gray-50 text-gray-500 border-gray-200' },
};

function CategoryBadge({ category }: { category: string }) {
  const style = CATEGORY_STYLES[category] ?? CATEGORY_STYLES.general;
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${style.cls}`}
    >
      {style.label}
    </span>
  );
}

// ── 新增/编辑 Dialog ────────────────────────────────────────────────────────

function ExperienceDialog({
  entry,
  onClose,
}: {
  entry: ExperienceEntry | null;
  onClose: () => void;
}) {
  const createMutation = useCreateExperience();
  const updateMutation = useUpdateExperience();
  const isEdit = entry != null;
  const titleId = useId();

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const [title, setTitle] = useState(entry?.title ?? '');
  const [content, setContent] = useState(entry?.content ?? '');
  const [routePattern, setRoutePattern] = useState(entry?.route_pattern ?? '通用');
  const [category, setCategory] = useState(entry?.category ?? 'general');

  const isPending = createMutation.isPending || updateMutation.isPending;
  const isError = createMutation.isError || updateMutation.isError;
  const canSubmit = title.trim().length > 0 && content.trim().length > 0 && !isPending;

  const handleSubmit = () => {
    if (isEdit) {
      updateMutation.mutate(
        {
          id: entry.id,
          body: {
            title: title.trim(),
            content: content.trim(),
            route_pattern: routePattern.trim() || '通用',
            category,
          },
        },
        { onSuccess: onClose },
      );
    } else {
      createMutation.mutate(
        {
          title: title.trim(),
          content: content.trim(),
          route_pattern: routePattern.trim() || '通用',
          category,
        },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 max-h-[calc(100vh-2rem)] overflow-y-auto"
      >
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 id={titleId} className="text-lg font-semibold text-gray-900">
            {isEdit ? '编辑经验' : '新增经验'}
          </h2>
          <p className="text-sm text-gray-400 mt-0.5">沉淀的买入经验将注入后续 AI 简报生成</p>
        </div>
        <div className="px-6 py-5 space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">标题 *</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="如：提前 30 天买最便宜"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1.5">内容 *</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={3}
              placeholder="具体可操作的买入经验…"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400 resize-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">适用航线</label>
              <input
                type="text"
                value={routePattern}
                onChange={(e) => setRoutePattern(e.target.value)}
                placeholder="通用 或 北京→上海"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1.5">分类</label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-amber-100 focus:border-amber-400"
              >
                <option value="general">通用</option>
                <option value="timing">时机</option>
                <option value="route">航线</option>
                <option value="holiday">节假日</option>
              </select>
            </div>
          </div>
          {isError && <p className="text-xs text-red-500">保存失败，请重试</p>}
        </div>
        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-500 hover:bg-gray-50 rounded-lg"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={handleSubmit}
            className="px-4 py-2 text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg disabled:opacity-40"
          >
            {isPending ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 经验库 Tab ──────────────────────────────────────────────────────────────

export function ExperiencesTab() {
  const [showArchived, setShowArchived] = useState(false);
  const { data: entries, isLoading } = useExperiences(showArchived ? '' : 'active');
  const updateMutation = useUpdateExperience();
  const deleteMutation = useDeleteExperience();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ExperienceEntry | null>(null);

  const list = (entries ?? []).filter((e) => showArchived || e.status === 'active');

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <label className="flex items-center gap-2 text-sm text-gray-500 cursor-pointer">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className="rounded border-gray-300 text-amber-600 focus:ring-amber-200"
          />
          显示已归档
        </label>
        <button
          type="button"
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
          className="px-4 py-2 text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 rounded-lg"
        >
          + 新增经验
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
              <div className="h-4 bg-gray-100 rounded w-1/3 mb-2" />
              <div className="h-3 bg-gray-50 rounded w-2/3" />
            </div>
          ))}
        </div>
      ) : list.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-400">
          <span className="text-4xl mb-4">📚</span>
          <p className="text-base">暂无买入经验</p>
          <p className="text-sm mt-1">买点分析完成后会自动沉淀经验，也可手动新增</p>
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((entry) => (
            <div
              key={entry.id}
              className={`bg-white rounded-xl border p-4 hover:shadow-sm transition-shadow ${
                entry.status === 'archived' ? 'border-gray-100 opacity-60' : 'border-gray-100'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                    <CategoryBadge category={entry.category} />
                    <span className="text-xs text-gray-400">{entry.route_pattern}</span>
                    <span className="text-xs text-gray-300 tabular-nums">
                      {entry.evidence_count > 0
                        ? `案例证据 ${entry.evidence_count} 次`
                        : '手工经验 · 未验证'}
                    </span>
                    {entry.status === 'archived' && (
                      <span className="text-xs text-gray-400">（已归档）</span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-gray-800 mb-1">{entry.title}</p>
                  <p className="text-xs text-gray-500 leading-relaxed">{entry.content}</p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(entry);
                      setDialogOpen(true);
                    }}
                    className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg"
                  >
                    编辑
                  </button>
                  {entry.status === 'active' ? (
                    <button
                      type="button"
                      onClick={() =>
                        updateMutation.mutate({ id: entry.id, body: { status: 'archived' } })
                      }
                      className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg"
                    >
                      归档
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() =>
                        updateMutation.mutate({ id: entry.id, body: { status: 'active' } })
                      }
                      className="px-2.5 py-1.5 text-xs text-amber-600 hover:bg-amber-50 rounded-lg"
                    >
                      恢复
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm('确定删除这条经验吗？')) {
                        deleteMutation.mutate(entry.id);
                      }
                    }}
                    className="px-2.5 py-1.5 text-xs text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg"
                  >
                    删除
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {dialogOpen && <ExperienceDialog entry={editing} onClose={() => setDialogOpen(false)} />}
    </div>
  );
}
