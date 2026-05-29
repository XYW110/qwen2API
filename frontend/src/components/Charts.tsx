import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  BarChart, Bar,
  PieChart, Pie, Cell,
  AreaChart, Area,
} from 'recharts'

// ============================================
// QpsChart - QPS 折线图
// ============================================
interface QpsChartProps {
  data: Array<{ time: string; qps: number; errors: number }>
}

export function QpsChart({ data }: QpsChartProps) {
  return (
    <div className="rounded-2xl border border-border/50 bg-card/30 backdrop-blur-xl shadow-2xl relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-black/[0.02] dark:from-white/[0.02] to-transparent pointer-events-none" />
      <div className="flex flex-col space-y-2 p-6 border-b border-border/50 bg-muted/10 relative z-10">
        <h3 className="font-extrabold text-xl tracking-tight flex items-center gap-3">
          <span className="bg-blue-500 w-2 h-6 rounded-full shadow-[0_0_10px_rgba(59,130,246,0.5)]"></span>
          QPS 趋势
        </h3>
        <p className="text-sm text-muted-foreground ml-5">请求速率与错误统计</p>
      </div>
      <div className="p-6 relative z-10">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
            <XAxis dataKey="time" className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
            <YAxis className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
            <Tooltip
              contentStyle={{
                backgroundColor: 'hsl(var(--card))',
                border: '1px solid hsl(var(--border))',
                borderRadius: '8px',
              }}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="qps"
              stroke="#3b82f6"
              strokeWidth={2}
              dot={false}
              name="QPS"
            />
            <Line
              type="monotone"
              dataKey="errors"
              stroke="#ef4444"
              strokeWidth={2}
              strokeDasharray="5 5"
              dot={false}
              name="错误数"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ============================================
// LatencyChart - 延迟百分位图
// ============================================
interface LatencyChartProps {
  p50: number
  p95: number
  p99: number
}

export function LatencyChart({ p50, p95, p99 }: LatencyChartProps) {
  const data = [
    { name: 'P50', value: p50 * 1000, color: '#22c55e' },
    { name: 'P95', value: p95 * 1000, color: '#f97316' },
    { name: 'P99', value: p99 * 1000, color: '#ef4444' },
  ]

  return (
    <div className="rounded-2xl border border-border/50 bg-card/30 backdrop-blur-xl shadow-2xl relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-black/[0.02] dark:from-white/[0.02] to-transparent pointer-events-none" />
      <div className="flex flex-col space-y-2 p-6 border-b border-border/50 bg-muted/10 relative z-10">
        <h3 className="font-extrabold text-xl tracking-tight flex items-center gap-3">
          <span className="bg-emerald-500 w-2 h-6 rounded-full shadow-[0_0_10px_rgba(34,197,94,0.5)]"></span>
          延迟分布
        </h3>
        <p className="text-sm text-muted-foreground ml-5">响应时间百分位统计（毫秒）</p>
      </div>
      <div className="p-6 relative z-10">
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
            <XAxis dataKey="name" className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
            <YAxis className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
            <Tooltip
              contentStyle={{
                backgroundColor: 'hsl(var(--card))',
                border: '1px solid hsl(var(--border))',
                borderRadius: '8px',
              }}
              formatter={(value: number) => [`${value.toFixed(2)} ms`, '延迟']}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ============================================
// ModelPieChart - 模型分布饼图
// ============================================
interface ModelPieChartProps {
  data: Record<string, number>
}

const PIE_COLORS = [
  '#3b82f6', '#22c55e', '#f97316', '#ef4444', '#8b5cf6',
  '#ec4899', '#14b8a6', '#f59e0b', '#6366f1', '#84cc16',
]

export function ModelPieChart({ data }: ModelPieChartProps) {
  const chartData = Object.entries(data).map(([name, value]) => ({
    name,
    value,
  }))

  return (
    <div className="rounded-2xl border border-border/50 bg-card/30 backdrop-blur-xl shadow-2xl relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-black/[0.02] dark:from-white/[0.02] to-transparent pointer-events-none" />
      <div className="flex flex-col space-y-2 p-6 border-b border-border/50 bg-muted/10 relative z-10">
        <h3 className="font-extrabold text-xl tracking-tight flex items-center gap-3">
          <span className="bg-purple-500 w-2 h-6 rounded-full shadow-[0_0_10px_rgba(139,92,246,0.5)]"></span>
          模型分布
        </h3>
        <p className="text-sm text-muted-foreground ml-5">各模型请求占比</p>
      </div>
      <div className="p-6 relative z-10">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                paddingAngle={2}
                dataKey="value"
                nameKey="name"
                label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
                labelLine={false}
              >
                {chartData.map((_, index) => (
                  <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
                formatter={(value: number, name: string) => [value, name]}
              />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[300px] flex items-center justify-center text-muted-foreground">
            暂无数据
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================
// ErrorBarChart - 错误状态码柱状图
// ============================================
interface ErrorBarChartProps {
  data: Record<number, number>
}

export function ErrorBarChart({ data }: ErrorBarChartProps) {
  // 过滤出状态码 >= 400 的数据
  const chartData = Object.entries(data)
    .filter(([code]) => Number(code) >= 400)
    .map(([code, count]) => ({
      code: `${code}`,
      count,
      isServerError: Number(code) >= 500,
    }))
    .sort((a, b) => Number(a.code) - Number(b.code))

  return (
    <div className="rounded-2xl border border-border/50 bg-card/30 backdrop-blur-xl shadow-2xl relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-black/[0.02] dark:from-white/[0.02] to-transparent pointer-events-none" />
      <div className="flex flex-col space-y-2 p-6 border-b border-border/50 bg-muted/10 relative z-10">
        <h3 className="font-extrabold text-xl tracking-tight flex items-center gap-3">
          <span className="bg-red-500 w-2 h-6 rounded-full shadow-[0_0_10px_rgba(239,68,68,0.5)]"></span>
          错误状态码分布
        </h3>
        <p className="text-sm text-muted-foreground ml-5">HTTP 错误响应统计（4xx/5xx）</p>
      </div>
      <div className="p-6 relative z-10">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
              <XAxis dataKey="code" className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
              <YAxis className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
                formatter={(value: number) => [value, '次数']}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {chartData.map((entry, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={entry.isServerError ? '#ef4444' : '#f97316'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[300px] flex items-center justify-center text-muted-foreground">
            暂无错误数据
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================
// TokenAreaChart - Token 消耗趋势面积图
// ============================================
interface TokenAreaChartProps {
  data: Array<{ time: string; prompt_tokens: number; completion_tokens: number }>
}

export function TokenAreaChart({ data }: TokenAreaChartProps) {
  return (
    <div className="rounded-2xl border border-border/50 bg-card/30 backdrop-blur-xl shadow-2xl relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-black/[0.02] dark:from-white/[0.02] to-transparent pointer-events-none" />
      <div className="flex flex-col space-y-2 p-6 border-b border-border/50 bg-muted/10 relative z-10">
        <h3 className="font-extrabold text-xl tracking-tight flex items-center gap-3">
          <span className="bg-blue-500 w-2 h-6 rounded-full shadow-[0_0_10px_rgba(59,130,246,0.5)]"></span>
          Token 消耗趋势
        </h3>
        <p className="text-sm text-muted-foreground ml-5">Prompt vs Completion 时序统计</p>
      </div>
      <div className="p-6 relative z-10">
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
              <XAxis dataKey="time" className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
              <YAxis className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
              />
              <Legend />
              <Area
                type="monotone"
                dataKey="prompt_tokens"
                stroke="#3b82f6"
                fill="#3b82f6"
                fillOpacity={0.2}
                strokeWidth={2}
                name="Prompt Tokens"
              />
              <Area
                type="monotone"
                dataKey="completion_tokens"
                stroke="#8b5cf6"
                fill="#8b5cf6"
                fillOpacity={0.2}
                strokeWidth={2}
                name="Completion Tokens"
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[300px] flex items-center justify-center text-muted-foreground">
            暂无数据
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================
// TokenModelBarChart - 按模型 Token 分布柱状图
// ============================================
interface TokenModelBarChartProps {
  data: Record<string, { prompt: number; completion: number }>
}

export function TokenModelBarChart({ data }: TokenModelBarChartProps) {
  const chartData = Object.entries(data).map(([model, tokens]) => ({
    model,
    prompt: tokens.prompt,
    completion: tokens.completion,
  }))

  return (
    <div className="rounded-2xl border border-border/50 bg-card/30 backdrop-blur-xl shadow-2xl relative overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-black/[0.02] dark:from-white/[0.02] to-transparent pointer-events-none" />
      <div className="flex flex-col space-y-2 p-6 border-b border-border/50 bg-muted/10 relative z-10">
        <h3 className="font-extrabold text-xl tracking-tight flex items-center gap-3">
          <span className="bg-violet-500 w-2 h-6 rounded-full shadow-[0_0_10px_rgba(139,92,246,0.5)]"></span>
          按模型 Token 分布
        </h3>
        <p className="text-sm text-muted-foreground ml-5">各模型 Prompt 与 Completion 消耗对比</p>
      </div>
      <div className="p-6 relative z-10">
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
              <XAxis dataKey="model" className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
              <YAxis className="text-xs" tick={{ fill: 'hsl(var(--muted-foreground))' }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'hsl(var(--card))',
                  border: '1px solid hsl(var(--border))',
                  borderRadius: '8px',
                }}
              />
              <Legend />
              <Bar dataKey="prompt" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Prompt" />
              <Bar dataKey="completion" fill="#8b5cf6" radius={[4, 4, 0, 0]} name="Completion" />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[300px] flex items-center justify-center text-muted-foreground">
            暂无数据
          </div>
        )}
      </div>
    </div>
  )
}
