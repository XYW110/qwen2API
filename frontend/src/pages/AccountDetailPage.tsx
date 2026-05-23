import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Loader2, AlertCircle, Activity, Zap, Clock } from "lucide-react";
import { Button } from "../components/ui/button";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";
import { toast } from "sonner";

type ModelStat = {
  tok_s_ema: number;
  total_tokens: number;
  request_count: number;
};

type HourlyUsage = {
  hour_key: string;
  prompt_tokens: number;
  completion_tokens: number;
};

type AccountStats = {
  email: string;
  model_stats: Record<string, ModelStat>;
  hourly_usage: HourlyUsage[];
};

export default function AccountDetailPage() {
  const { email } = useParams<{ email: string }>();
  const [stats, setStats] = useState<AccountStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!email) return;
    
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`${API_BASE}/api/admin/accounts/${encodeURIComponent(email)}/stats`, {
      headers: getAuthHeader(),
    })
      .then((res) => {
        if (!res.ok) throw new Error("获取统计数据失败");
        return res.json();
      })
      .then((data) => {
        if (!cancelled) {
          setStats(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || "未知错误");
          setLoading(false);
          toast.error("加载账户详情失败");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [email]);

  // 计算图表最大值用于归一化
  const maxTokens = stats
    ? Math.max(
        ...stats.hourly_usage.map((h) => h.prompt_tokens + h.completion_tokens),
        1
      )
    : 1;

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !stats) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
        <AlertCircle className="h-12 w-12 text-destructive" />
        <p className="text-lg font-medium">{error || "数据加载失败"}</p>
        <Link to="/accounts">
          <Button variant="outline">返回账号列表</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 顶部导航 */}
      <div className="flex items-center gap-4">
        <Link to="/accounts">
          <Button variant="ghost" size="sm" className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            返回
          </Button>
        </Link>
        <div>
          <h2 className="text-2xl font-bold tracking-tight">{stats.email}</h2>
          <p className="text-sm text-muted-foreground">账户性能与用量详情</p>
        </div>
      </div>

      {/* 基本信息卡片 */}
      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
            <Activity className="h-4 w-4" />
            模型数量
          </div>
          <div className="text-2xl font-bold">
            {Object.keys(stats.model_stats).length}
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
            <Zap className="h-4 w-4" />
            总请求数
          </div>
          <div className="text-2xl font-bold">
            {Object.values(stats.model_stats).reduce(
              (sum, m) => sum + m.request_count,
              0
            )}
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
            <Clock className="h-4 w-4" />
            24h Token 总量
          </div>
          <div className="text-2xl font-bold">
            {stats.hourly_usage
              .reduce((sum, h) => sum + h.prompt_tokens + h.completion_tokens, 0)
              .toLocaleString()}
          </div>
        </div>
      </div>

      {/* 模型性能统计表 */}
      <div className="rounded-2xl border bg-card/30 overflow-hidden">
        <div className="p-6 border-b bg-muted/10">
          <h3 className="text-lg font-bold">模型性能统计</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-muted/30 border-b text-muted-foreground text-xs uppercase tracking-wider font-semibold">
              <tr>
                <th className="h-12 px-6 align-middle">模型名称</th>
                <th className="h-12 px-6 align-middle text-right">tok/s (EMA)</th>
                <th className="h-12 px-6 align-middle text-right">总 Tokens</th>
                <th className="h-12 px-6 align-middle text-right">请求次数</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {Object.entries(stats.model_stats).length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center text-muted-foreground">
                    暂无模型统计数据
                  </td>
                </tr>
              ) : (
                Object.entries(stats.model_stats).map(([model, stat]) => (
                  <tr key={model} className="transition-colors hover:bg-black/5 dark:hover:bg-white/5">
                    <td className="px-6 py-4 align-middle font-medium font-mono">
                      {model}
                    </td>
                    <td className="px-6 py-4 align-middle text-right font-mono">
                      {stat.tok_s_ema.toFixed(1)}
                    </td>
                    <td className="px-6 py-4 align-middle text-right font-mono">
                      {stat.total_tokens.toLocaleString()}
                    </td>
                    <td className="px-6 py-4 align-middle text-right font-mono">
                      {stat.request_count.toLocaleString()}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 24h 用量图表 */}
      <div className="rounded-2xl border bg-card/30 p-6">
        <h3 className="text-lg font-bold mb-6">24 小时用量趋势</h3>
        
        {stats.hourly_usage.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-muted-foreground">
            暂无用量数据
          </div>
        ) : (
          <div className="space-y-4">
            {/* SVG 柱状图 */}
            <div className="h-48 w-full relative">
              <svg className="w-full h-full" viewBox="0 0 1000 200" preserveAspectRatio="none">
                {/* 网格线 */}
                {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
                  <line
                    key={ratio}
                    x1="0"
                    y1={200 - ratio * 200}
                    x2="1000"
                    y2={200 - ratio * 200}
                    stroke="currentColor"
                    strokeOpacity="0.1"
                    strokeWidth="1"
                  />
                ))}
                
                {/* 柱子 */}
                {stats.hourly_usage.map((hour, i) => {
                  const total = hour.prompt_tokens + hour.completion_tokens;
                  const height = (total / maxTokens) * 180;
                  const barWidth = 1000 / stats.hourly_usage.length;
                  const x = i * barWidth;
                  
                  return (
                    <g key={hour.hour_key}>
                      {/* Completion tokens (上方) */}
                      <rect
                        x={x + barWidth * 0.1}
                        y={200 - height}
                        width={barWidth * 0.8}
                        height={(hour.completion_tokens / maxTokens) * 180}
                        className="fill-blue-500/70"
                        rx="2"
                      />
                      {/* Prompt tokens (下方) */}
                      <rect
                        x={x + barWidth * 0.1}
                        y={200 - (hour.prompt_tokens / maxTokens) * 180}
                        width={barWidth * 0.8}
                        height={(hour.prompt_tokens / maxTokens) * 180}
                        className="fill-emerald-500/70"
                        rx="2"
                      />
                    </g>
                  );
                })}
              </svg>
            </div>

            {/* 图例 */}
            <div className="flex items-center justify-center gap-6 text-xs text-muted-foreground">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-sm bg-emerald-500/70" />
                <span>Prompt Tokens</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-sm bg-blue-500/70" />
                <span>Completion Tokens</span>
              </div>
            </div>

            {/* 时间轴标签（简化显示） */}
            <div className="flex justify-between text-xs text-muted-foreground font-mono px-2">
              {stats.hourly_usage.filter((_, i) => i % 4 === 0).map((hour) => {
                const date = new Date(hour.hour_key);
                return (
                  <span key={hour.hour_key}>
                    {date.getHours().toString().padStart(2, "0")}:00
                  </span>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
