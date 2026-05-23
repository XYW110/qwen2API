import { useState, useEffect } from "react";
import { Button } from "./ui/button";
import { toast } from "sonner";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";
import { Settings2, Save, X } from "lucide-react";

// 策略选项
type Strategy = "least_loaded" | "least_used" | "round_robin";

interface StrategyConfigData {
  strategy: Strategy;
  maxFailuresBeforeCooldown: number;
  cooldownPeriodSeconds: number;
}
interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export function StrategyConfigModal({ isOpen, onClose }: Props) {
  const [config, setConfig] = useState<StrategyConfigData>({
    strategy: "least_loaded",
    maxFailuresBeforeCooldown: 3,
    cooldownPeriodSeconds: 300,
  });
  const [loading, setLoading] = useState(false);

  // 从后端获取当前配置（必须在 hooks 规则下定义）
  const loadConfig = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE}/api/admin/settings`, {
        headers: getAuthHeader(),
      });

      if (response.ok) {
        const data = await response.json();

        // 从后端获取的配置数据
        if (data.account_selection_strategy) {
          setConfig({
            strategy: data.account_selection_strategy,
            maxFailuresBeforeCooldown:
              data.account_max_failures_before_cooldown || 3,
            cooldownPeriodSeconds: data.account_cooldown_period_seconds || 300,
          });
        }
      }
    } catch (error) {
      console.error("Failed to load config", error);
      toast.error("加载配置失败");
    } finally {
      setLoading(false);
    }
  };

  // 组件加载时获取配置（只在模态框打开时加载）
  useEffect(() => {
    if (isOpen) {
      loadConfig();
    }
  }, [isOpen]);

  // 保存配置到后端
  const handleSave = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE}/api/admin/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...getAuthHeader() },
        body: JSON.stringify({
          account_selection_strategy: config.strategy,
          account_max_failures_before_cooldown:
            config.maxFailuresBeforeCooldown,
          account_cooldown_period_seconds: config.cooldownPeriodSeconds,
        }),
      });

      const data = await response.json();

      if (data.ok) {
        toast.success("配置保存成功", {
          description: "策略配置已更新，立即生效",
        });
        onClose();
      } else {
        toast.error(data.error || "配置保存失败");
      }
    } catch (error) {
      toast.error("请求失败");
    } finally {
      setLoading(false);
    }
  };

  // 加载当前配置
  const refreshConfig = () => {
    loadConfig();
  };

  // 如果模态框未打开，则不渲染任何内容（必须在所有 hooks 之后）
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative z-10 w-full max-w-md mx-4 bg-background rounded-xl border shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <div className="flex items-center gap-2">
            <Settings2 className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">账号策略配置</h2>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            className="h-8 w-8 p-0"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-6 overflow-y-auto">
          {/* 当前配置说明 */}
          <div className="rounded-lg bg-muted p-3 text-sm text-muted-foreground">
            <p className="mb-2">账号选择策略支持以下三种模式：</p>
            <ul className="space-y-1 ml-4">
              <li>
                <strong>least_loaded</strong> -
                选择当前并发负载最低的账号（默认策略）
              </li>
              <li>
                <strong>least_used</strong> - 选择最久未使用的账号，均匀分配负载
              </li>
              <li>
                <strong>round_robin</strong> - 轮询分配，依次使用每个账号
              </li>
            </ul>
          </div>

          {/* 失败冷却配置 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">
                连续失败次数达阈值进入冷却
              </label>
              <input
                type="number"
                min={1}
                max={10}
                value={config.maxFailuresBeforeCooldown}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    maxFailuresBeforeCooldown: parseInt(e.target.value),
                  })
                }
                className="w-20 h-9 px-2 rounded-md border border-input bg-background text-sm"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              达到阈值后账号将进入冷却期，冷却期结束后自动恢复
            </p>
          </div>

          {/* 冷却时长配置 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium">冷却时长（秒）</label>
              <input
                type="number"
                min={60}
                max={3600}
                value={config.cooldownPeriodSeconds}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    cooldownPeriodSeconds: parseInt(e.target.value),
                  })
                }
                className="w-24 h-9 px-2 rounded-md border border-input bg-background text-sm"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              冷却期结束后账号将自动恢复可用状态
            </p>
          </div>

          {/* 策略选择 */}
          <div className="space-y-3">
            <label className="text-sm font-medium">账号选择策略</label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { value: "least_loaded", label: "最低负载", desc: "选最闲的" },
                { value: "least_used", label: "最久未用", desc: "均匀分配" },
                { value: "round_robin", label: "轮询", desc: "依次使用" },
              ].map((item) => (
                <button
                  key={item.value}
                  onClick={() =>
                    setConfig({ ...config, strategy: item.value as Strategy })
                  }
                  className={`flex flex-col items-center justify-center p-3 rounded-lg border transition-all ${
                    config.strategy === item.value
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-input hover:bg-muted"
                  }`}
                >
                  <span className="font-medium">{item.label}</span>
                  <span className="text-xs text-muted-foreground mt-1">
                    {item.desc}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* 配置说明 */}
          <div className="rounded-lg bg-green-500/10 border border-green-500/20 p-3">
            <p className="text-sm text-green-700 dark:text-green-300">
              <strong>提示：</strong>配置保存后立即生效，无需重启服务
            </p>
            <p className="text-xs text-green-700/80 dark:text-green-300/80 mt-2">
              配置持久化在内存中，服务重启后会恢复为默认值。如需永久配置，可在后端环境变量中设置。
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t bg-muted/30">
          <Button variant="outline" onClick={refreshConfig}>
            刷新配置
          </Button>
          <Button onClick={handleSave} disabled={loading}>
            {loading ? (
              "保存中..."
            ) : (
              <>
                <Save className="mr-2 h-4 w-4" />
                保存配置
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
