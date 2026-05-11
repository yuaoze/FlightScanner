export function ComingSoonPage({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-32 text-gray-400">
      <span className="text-5xl mb-6">🚧</span>
      <h1 className="text-xl font-semibold text-gray-700 mb-2">{title}</h1>
      <p className="text-sm">功能开发中，即将上线</p>
    </div>
  );
}
