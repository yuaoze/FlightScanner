import { useRoutePredictions } from '../../hooks/useRouteDetail';
import { DecisionBadge } from '../dashboard/DecisionBadge';
import type { RouteDetailResponse, MonitorStatus } from '../../types';

interface Props {
  routeId: number;
  route: RouteDetailResponse;
}

const OUTCOME_STYLES: Record<string, { dot: string; label: string }> = {
  win: { dot: 'bg-green-500', label: '✓ 正确' },
  loss: { dot: 'bg-red-500', label: '✗ 错误' },
  neutral: { dot: 'bg-gray-400', label: '— 中性' },
  pending: { dot: 'bg-blue-400', label: '⏳ 待验证' },
};

export function AIInsightsTab({ routeId, route }: Props) {
  const { data, isLoading } = useRoutePredictions(routeId);

  return (
    <div className="space-y-4">
      {/* Current Recommendation */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">当前建议</h3>
        <div className="flex items-start gap-4">
          <DecisionBadge status={route.status as MonitorStatus} />
          <div className="flex-1">
            <p className="text-sm text-gray-700">{route.prediction_text}</p>
            {route.trend_confidence > 0 && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                  <span>置信度</span>
                  <span>{Math.round(route.trend_confidence * 100)}%</span>
                </div>
                <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all"
                    style={{ width: `${route.trend_confidence * 100}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Win Rate Stats */}
      {data && data.total > 0 && (
        <div className="bg-white rounded-xl border border-gray-100 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">该路线预测统计</h3>
          <div className="flex items-center gap-6">
            <div className="text-center">
              <p className="text-2xl font-bold text-gray-900">
                {data.win_rate !== null ? `${data.win_rate}%` : '--'}
              </p>
              <p className="text-xs text-gray-500">准确率</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-gray-900">{data.total}</p>
              <p className="text-xs text-gray-500">总预测</p>
            </div>
          </div>
        </div>
      )}

      {/* Prediction Timeline */}
      <div className="bg-white rounded-xl border border-gray-100 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">预测历史</h3>
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-12 bg-gray-50 rounded animate-pulse" />
            ))}
          </div>
        ) : !data || data.predictions.length === 0 ? (
          <p className="text-sm text-gray-400 text-center py-6">暂无 AI 预测记录</p>
        ) : (
          <div className="space-y-3">
            {data.predictions.slice(0, 20).map((pred) => {
              const style = OUTCOME_STYLES[pred.outcome_status] || OUTCOME_STYLES.pending;
              return (
                <div key={pred.id} className="flex items-center gap-3 py-2 border-b border-gray-50 last:border-0">
                  <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${style.dot}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                        pred.recommended_action === 'Buy'
                          ? 'bg-green-50 text-green-700'
                          : 'bg-orange-50 text-orange-700'
                      }`}>
                        {pred.recommended_action === 'Buy' ? '买入' : '等待'}
                      </span>
                      <span className="text-xs text-gray-500">
                        ¥{Math.round(pred.price_at_prediction)}
                      </span>
                      {pred.confidence !== null && (
                        <span className="text-xs text-gray-400">
                          {Math.round(pred.confidence * 100)}%
                        </span>
                      )}
                    </div>
                    {pred.reason && (
                      <p className="text-xs text-gray-400 mt-0.5 truncate">{pred.reason}</p>
                    )}
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-xs text-gray-400">{pred.predicted_at.slice(0, 10)}</p>
                    <p className="text-[10px] text-gray-400">{style.label}</p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
