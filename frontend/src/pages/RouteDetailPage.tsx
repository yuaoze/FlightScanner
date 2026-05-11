import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useRouteDetail } from '../hooks/useRouteDetail';
import { RouteDetailHeader } from '../components/route-detail/RouteDetailHeader';
import { TabNavigation } from '../components/route-detail/TabNavigation';
import type { DetailTab } from '../components/route-detail/TabNavigation';
import { PriceTrendsTab } from '../components/route-detail/PriceTrendsTab';
import { AIInsightsTab } from '../components/route-detail/AIInsightsTab';
import { FlightsTab } from '../components/route-detail/FlightsTab';
import { ConfigTab } from '../components/route-detail/ConfigTab';

export function RouteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const routeId = Number(id);
  const { data: route, isLoading } = useRouteDetail(routeId);
  const [activeTab, setActiveTab] = useState<DetailTab>('price');

  if (isLoading || !route) {
    return (
      <div>
        <div className="bg-white rounded-xl border border-gray-100 p-5 mb-4 animate-pulse">
          <div className="h-4 bg-gray-100 rounded w-16 mb-4" />
          <div className="h-6 bg-gray-100 rounded w-1/3 mb-2" />
          <div className="h-4 bg-gray-50 rounded w-1/2" />
        </div>
        <div className="h-8 bg-gray-50 rounded w-2/3 mb-5" />
        <div className="bg-white rounded-xl border border-gray-100 p-5 h-[300px] animate-pulse">
          <div className="h-full bg-gray-50 rounded" />
        </div>
      </div>
    );
  }

  return (
    <div>
      <RouteDetailHeader route={route} />
      <TabNavigation activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'price' && (
        <PriceTrendsTab routeId={routeId} targetPrice={route.target_price} />
      )}
      {activeTab === 'ai' && <AIInsightsTab routeId={routeId} route={route} />}
      {activeTab === 'flights' && <FlightsTab routeId={routeId} route={route} />}
      {activeTab === 'config' && <ConfigTab routeId={routeId} route={route} />}
    </div>
  );
}
