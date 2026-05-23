import { useEffect, useMemo, useState } from "react";
import { Button } from "../components/ui/button";
import {
  Trash2,
  Plus,
  RefreshCw,
  Bot,
  ShieldCheck,
  MailWarning,
  Brain,
  Eraser,
  MessageSquareX,
  Settings2,
  Upload,
  Download,
} from "lucide-react";
import { toast } from "sonner";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";
import { checkRegisterUnlock } from "../lib/registerUnlock";
import { BatchImportModal } from "../components/BatchImportModal";
import { BatchExportModal } from "../components/BatchExportModal";
import { StrategyConfigModal } from "../components/StrategyConfig";

type AccountItem = {
  email: string;
  password?: string;
  token?: string;
  username?: string;
  proxy?: string;
  valid?: boolean;
  inflight?: number;
  rate_limited_until?: number;
  activation_pending?: boolean;
  status_code?: string;
  status_text?: string;
  last_error?: string;
  consecutive_failures?: number;
  cooldown_started_at?: number;
  is_in_cooldown?: boolean;
  selection_block_reason?: string;
  cooldown_ends_at?: number;
};

function statusStyle(code?: string) {
  switch (code) {
    case "valid":
      return "bg-green-500/10 text-green-700 dark:text-green-400 ring-green-500/20";
    case "pending_activation":
      return "bg-orange-500/10 text-orange-700 dark:text-orange-400 ring-orange-500/20";
    case "rate_limited":
      return "bg-yellow-500/10 text-yellow-700 dark:text-yellow-300 ring-yellow-500/20";
    case "banned":
      return "bg-red-500/10 text-red-700 dark:text-red-400 ring-red-500/20";
    case "auth_error":
      return "bg-slate-500/10 text-slate-700 dark:text-slate-300 ring-slate-500/20";
    default:
      return "bg-red-500/10 text-red-700 dark:text-red-400 ring-red-500/20";
  }
}

function statusText(acc: AccountItem) {
  if (acc.is_in_cooldown) return "冷却中";
  switch (acc.status_code) {
    case "valid":
      return "可用";
    case "pending_activation":
      return "未激活";
    case "rate_limited":
      return "限流";
    case "banned":
      return "封禁";
    case "auth_error":
      return "认证失效";
    case "cooldown":
      return "冷却中";
    default:
      return acc.valid ? "可用" : "失效";
  }
}

function statusNote(acc: AccountItem) {
  if (acc.is_in_cooldown && acc.cooldown_ends_at) {
    const remaining = Math.max(0, acc.cooldown_ends_at - Date.now() / 1000);
    if (remaining > 0) {
      const minutes = Math.floor(remaining / 60);
      const seconds = Math.ceil(remaining % 60);
      return `剩余冷却时间：${minutes}分${seconds}秒`;
    }
  }
  if ((acc.rate_limited_until || 0) > Date.now() / 1000) {
    const seconds = Math.max(
      0,
      Math.ceil(acc.rate_limited_until! - Date.now() / 1000)
    );
    return `预计 ${seconds} 秒后恢复`;
  }
  return acc.last_error || "";
}

function localizeError(error?: string) {
  if (!error) return "未知错误";
  const lower = error.toLowerCase();
  if (lower.includes("activation already in progress"))
    return "账号正在激活中，请稍后刷新";
  if (lower.includes("activation link or token not found"))
    return "激活链接或 Token 获取失败";
  if (
    lower.includes("token invalid") ||
    lower.includes("token") ||
    lower.includes("auth")
  )
    return "Token 无效或认证失败";
  return error;
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [proxy, setProxy] = useState("");
  const [registering, setRegistering] = useState(false);
  const [registerUnlocked, setRegisterUnlocked] = useState(false);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyingAll, setVerifyingAll] = useState(false);
  const [isBatchImportOpen, setIsBatchImportOpen] = useState(false);
  const [isBatchExportOpen, setIsBatchExportOpen] = useState(false);
  const [isStrategyConfigOpen, setIsStrategyConfigOpen] = useState(false);

  // 邮箱+密码字段同时匹配时解锁注册功能
  useEffect(() => {
    let cancelled = false;

    checkRegisterUnlock(email, password).then((unlocked) => {
      if (!cancelled && unlocked) setRegisterUnlocked(true);
    });

    return () => {
      cancelled = true;
    };
  }, [email, password]);

  const fetchAccounts = () => {
    fetch(`${API_BASE}/api/admin/accounts`, { headers: getAuthHeader() })
      .then((res) => {
        if (!res.ok) throw new Error("unauthorized");
        return res.json();
      })
      .then((data) => setAccounts(data.accounts || []))
      .catch(() => toast.error("刷新账号列表失败，请检查会话密钥"));
  };

  useEffect(() => {
    fetchAccounts();
  }, []);

  const stats = useMemo(() => {
    const result = {
      valid: 0,
      pending: 0,
      rateLimited: 0,
      banned: 0,
      cooling: 0,
      invalid: 0,
    };
    for (const acc of accounts) {
      if (acc.is_in_cooldown) {
        result.cooling += 1;
      } else {
        switch (acc.status_code) {
          case "valid":
            result.valid += 1;
            break;
          case "pending_activation":
            result.pending += 1;
            break;
          case "rate_limited":
            result.rateLimited += 1;
            break;
          case "banned":
            result.banned += 1;
            break;
          default:
            result.invalid += 1;
            break;
        }
      }
    }
    return result;
  }, [accounts]);

  const handleAdd = () => {
    // 优先使用 Token 方式（如果提供了）
    if (token.trim()) {
      const id = toast.loading("正在注入账号...");
      fetch(`${API_BASE}/api/admin/accounts`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeader() },
        body: JSON.stringify({
          email: email || `manual_${Date.now()}@qwen`,
          password,
          token,
          proxy,
        }),
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.ok) {
            toast.success("账号已加入账号池", { id });
            setEmail("");
            setPassword("");
            setToken("");
            setProxy("");
            fetchAccounts();
          } else {
            toast.error(localizeError(data.error) || "账号注入失败", {
              id,
              duration: 8000,
            });
          }
        })
        .catch(() => toast.error("账号注入请求失败", { id }));
    } else if (email && password) {
      // 使用邮箱密码登录方式
      const id = toast.loading("正在通过邮箱密码登录...");
      fetch(`${API_BASE}/api/admin/accounts`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeader() },
        body: JSON.stringify({
          email,
          password,
          proxy,
        }),
      })
        .then((res) => res.json())
        .then((data) => {
          if (data.ok) {
            toast.success("账号已加入账号池", { id });
            setEmail("");
            setPassword("");
            setProxy("");
            fetchAccounts();
          } else {
            toast.error(localizeError(data.error) || "账号添加失败", {
              id,
              duration: 8000,
            });
          }
        })
        .catch(() => toast.error("账号添加请求失败", { id }));
    } else {
      toast.error("请填写 Token 或 邮箱+密码");
      return;
    }
  };

  const handleDelete = (targetEmail: string) => {
    const id = toast.loading(`正在删除 ${targetEmail}...`);
    fetch(`${API_BASE}/api/admin/accounts/${encodeURIComponent(targetEmail)}`, {
      method: "DELETE",
      headers: getAuthHeader(),
    })
      .then((res) => {
        if (!res.ok) throw new Error("delete failed");
        toast.success(`已删除 ${targetEmail}`, { id });
        fetchAccounts();
      })
      .catch(() => toast.error("删除账号失败", { id }));
  };

  const handleAutoRegister = () => {
    setRegistering(true);
    const id = toast.loading("正在自动注册新账号，请稍候...");
    fetch(`${API_BASE}/api/admin/accounts/register`, {
      method: "POST",
      headers: getAuthHeader(),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.activation_pending) {
          toast.warning(`账号已注册，但仍需激活：${data.email}`, {
            id,
            duration: 8000,
          });
          fetchAccounts();
        } else if (data.ok) {
          toast.success(data.message || `注册成功：${data.email}`, {
            id,
            duration: 8000,
          });
          fetchAccounts();
        } else {
          toast.error(localizeError(data.error) || "自动注册失败", {
            id,
            duration: 8000,
          });
          if (data.email) fetchAccounts();
        }
      })
      .catch(() => toast.error("自动注册请求失败", { id }))
      .finally(() => setRegistering(false));
  };

  const handleVerify = (targetEmail: string) => {
    setVerifying(targetEmail);
    const id = toast.loading(`正在验证 ${targetEmail}...`);
    fetch(
      `${API_BASE}/api/admin/accounts/${encodeURIComponent(
        targetEmail
      )}/verify`,
      {
        method: "POST",
        headers: getAuthHeader(),
      }
    )
      .then((res) => res.json())
      .then((data) => {
        if (data.valid) {
          toast.success(`验证通过：${targetEmail}`, { id });
        } else {
          toast.error(
            `验证失败：${statusText(data) || localizeError(data.error)}`,
            { id, duration: 8000 }
          );
        }
        fetchAccounts();
      })
      .catch(() => toast.error("验证请求失败", { id }))
      .finally(() => setVerifying(null));
  };

  const handleVerifyAll = () => {
    setVerifyingAll(true);
    const id = toast.loading("正在并发巡检所有账号...");
    fetch(`${API_BASE}/api/admin/verify`, {
      method: "POST",
      headers: getAuthHeader(),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.ok) {
          toast.success(`全量巡检完成，并发数：${data.concurrency || 1}`, {
            id,
          });
        } else {
          toast.error("全量巡检失败", { id });
        }
        fetchAccounts();
      })
      .catch(() => toast.error("全量巡检请求失败", { id }))
      .finally(() => setVerifyingAll(false));
  };

  const handleActivate = (targetEmail: string) => {
    const id = toast.loading(`正在激活 ${targetEmail}...`);
    fetch(
      `${API_BASE}/api/admin/accounts/${encodeURIComponent(
        targetEmail
      )}/activate`,
      {
        method: "POST",
        headers: getAuthHeader(),
      }
    )
      .then((res) => res.json())
      .then((data) => {
        if (data.pending) {
          toast.success(`账号正在激活中，请稍后刷新：${targetEmail}`, {
            id,
            duration: 6000,
          });
        } else if (data.ok) {
          toast.success(data.message || `激活成功：${targetEmail}`, {
            id,
            duration: 6000,
          });
        } else {
          toast.error(
            `激活失败：${localizeError(data.error || data.message)}`,
            { id, duration: 8000 }
          );
        }
        fetchAccounts();
      })
      .catch(() => toast.error("激活请求失败", { id }));
  };

  const handleAccountAction = async (
    targetEmail: string,
    path: string,
    loadingMessage: string,
    successMessage: string,
    fallbackError: string
  ) => {
    const id = toast.loading(`${loadingMessage} ${targetEmail}...`);
    try {
      const res = await fetch(
        `${API_BASE}/api/admin/accounts/${encodeURIComponent(
          targetEmail
        )}/${path}`,
        {
          method: "POST",
          headers: getAuthHeader(),
        }
      );
      const data = await res.json().catch(() => ({}));

      if (data.ok) {
        toast.success(`${successMessage}：${targetEmail}`, { id });
      } else {
        toast.error(data.body || data.error || fallbackError, {
          id,
          duration: 8000,
        });
      }
    } catch {
      toast.error(fallbackError, { id });
    }
  };

  const handleDisableUpdateMemory = (targetEmail: string) => {
    handleAccountAction(
      targetEmail,
      "settings/disable-update-memory",
      "正在关闭更新记忆",
      "已关闭更新记忆",
      "关闭更新记忆失败"
    );
  };

  const handleDisableMemory = (targetEmail: string) => {
    handleAccountAction(
      targetEmail,
      "settings/disable-memory",
      "正在关闭记忆",
      "已关闭记忆",
      "关闭记忆失败"
    );
  };

  const handleClearMemories = (targetEmail: string) => {
    handleAccountAction(
      targetEmail,
      "memories/clear",
      "正在清空记忆",
      "已清空记忆",
      "清空记忆失败"
    );
  };

  const handleClearChats = (targetEmail: string) => {
    handleAccountAction(
      targetEmail,
      "chats/clear",
      "正在清空聊天记录",
      "已清空聊天记录",
      "清空聊天记录失败"
    );
  };

  return (
    <div className="space-y-6 relative">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-extrabold tracking-tight">
            {"账号管理"}
          </h2>
          <p className="text-muted-foreground mt-1">
            {"统一管理上游账号池，并区分未激活、限流、封禁与失效状态。"}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={handleVerifyAll}
            disabled={verifyingAll}
          >
            <ShieldCheck
              className={`mr-2 h-4 w-4 ${verifyingAll ? "animate-pulse" : ""}`}
            />{" "}
            {"全量巡检"}
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              fetchAccounts();
              toast.success("账号列表已刷新");
            }}
          >
            <RefreshCw className="mr-2 h-4 w-4" /> {"刷新状态"}
          </Button>
          <Button variant="outline" onClick={() => setIsBatchImportOpen(true)}>
            <Upload className="mr-2 h-4 w-4" /> {"批量导入"}
          </Button>
          <Button variant="outline" onClick={() => setIsBatchExportOpen(true)}>
            <Download className="mr-2 h-4 w-4" /> {"批量导出"}
          </Button>
          <Button
            variant="outline"
            onClick={() => setIsStrategyConfigOpen(true)}
          >
            <Settings2 className="mr-2 h-4 w-4" /> {"策略配置"}
          </Button>
          {registerUnlocked && (
            <Button
              variant="default"
              onClick={handleAutoRegister}
              disabled={registering}
            >
              {registering ? (
                <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Bot className="mr-2 h-4 w-4" />
              )}
              {registering ? "正在注册..." : "一键获取新号"}
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-5">
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground">{"可用"}</div>
          <div className="text-2xl font-bold">{stats.valid}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground">{"未激活"}</div>
          <div className="text-2xl font-bold">{stats.pending}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground">{"限流"}</div>
          <div className="text-2xl font-bold">{stats.rateLimited}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground">{"封禁"}</div>
          <div className="text-2xl font-bold">{stats.banned}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground">{"其他失效"}</div>
          <div className="text-2xl font-bold">{stats.invalid}</div>
        </div>
      </div>

      <div className="rounded-2xl border bg-card/40 p-6 space-y-4">
        <div>
          <h3 className="text-base font-bold">{"手动注入账号"}</h3>
          <p className="text-sm text-muted-foreground">
            {
              "请先在 chat.qwen.ai 登录，然后按 F12 打开开发者工具，在 Application / Storage 里的 Local Storage / 本地存储 中找到 token 并直接复制完整原始值粘贴到下方输入框。"
            }
          </p>
          <div className="rounded-xl border border-orange-500/30 bg-orange-500/10 p-3 mt-3">
            <p className="text-sm font-semibold text-orange-700 dark:text-orange-300">
              {
                "重要：请只粘贴 Local Storage / 本地存储 里的 token 原始值，不要从 Network 请求或 Authorization 请求头中提取。"
              }
            </p>
            <p className="text-xs text-orange-700/80 dark:text-orange-200/80 mt-1">
              {
                "请不要带 Bearer 前缀，也不要粘贴整段 Authorization 文本。支持和邮箱密码登录两种方式添加账号。"
              }
            </p>
          </div>
        </div>

        <div className="flex flex-col md:flex-row gap-4 items-end">
          {/* 邮箱输入 - 必填 */}
          <div className="flex-1 w-full">
            <label className="text-xs font-semibold mb-1.5 block">
              {"邮箱"}
            </label>
            <input
              type="text"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder={"user@example.com"}
            />
          </div>

          {/* 密码输入 - 必填 */}
          <div className="w-full md:w-48">
            <label className="text-xs font-semibold mb-1.5 block">
              {"密码"}
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder={"登录密码"}
            />
          </div>

          {/* Token 输入 - 可选 */}
          <div className="w-full md:w-80">
            <label className="text-xs font-semibold mb-1.5 block">
              {"Token（可选）"}
            </label>
            <input
              type="text"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder={"填写后优先使用 Token 方式"}
            />
          </div>

          {/* 代理输入 - 可选 */}
          <div className="w-full md:w-48">
            <label className="text-xs font-semibold mb-1.5 block">
              {"代理（可选）"}
            </label>
            <input
              type="text"
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder={"https://proxy:8080"}
            />
          </div>

          <Button
            onClick={handleAdd}
            variant="secondary"
            className="h-10 w-full md:w-auto font-semibold"
          >
            <Plus className="mr-2 h-4 w-4" /> {"添加账号"}
          </Button>
        </div>
      </div>

      <div className="rounded-2xl border bg-card/30 overflow-hidden">
        <div className="flex items-center justify-between p-6 border-b bg-muted/10">
          <h3 className="text-xl font-bold">{"账号列表"}</h3>
          <span className="inline-flex items-center justify-center bg-primary/10 text-primary rounded-full px-3 py-1 text-xs font-bold">
            {accounts.length}
          </span>
        </div>
        <table className="w-full text-sm text-left">
          <thead className="bg-muted/30 border-b text-muted-foreground text-xs uppercase tracking-wider font-semibold">
            <tr>
              <th className="h-12 px-6 align-middle">{"账号"}</th>
              <th className="h-12 px-6 align-middle">{"状态"}</th>
              <th className="h-12 px-6 align-middle">{"并发负载"}</th>
              <th className="h-12 px-6 align-middle">{"说明"}</th>
              <th className="h-12 px-6 align-middle text-right">{"操作"}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {accounts.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-6 py-12 text-center text-muted-foreground"
                >
                  {"暂无账号，请手动注入或一键获取新号。"}
                </td>
              </tr>
            )}
            {accounts.map((acc) => (
              <tr
                key={acc.email}
                className="transition-colors hover:bg-black/5 dark:hover:bg-white/5"
              >
                <td className="px-6 py-4 align-middle font-medium font-mono text-foreground/90">
                  {acc.email}
                </td>
                <td className="px-6 py-4 align-middle">
                  <span
                    className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold ring-1 ${statusStyle(
                      acc.status_code
                    )}`}
                  >
                    {statusText(acc)}
                  </span>
                </td>
                <td className="px-6 py-4 align-middle font-mono">
                  <span className="inline-flex items-center justify-center bg-muted/50 px-2 py-1 rounded text-xs border">
                    {acc.inflight || 0} {"线程"}
                  </span>
                </td>
                <td
                  className="px-6 py-4 align-middle text-muted-foreground max-w-[420px] truncate"
                  title={statusNote(acc)}
                >
                  {statusNote(acc) || "-"}
                </td>
                <td className="px-6 py-4 align-middle text-right">
                  <div className="flex items-center justify-end gap-1 flex-wrap">
                    {acc.status_code !== "valid" &&
                      acc.status_code !== "rate_limited" &&
                      acc.status_code !== "banned" && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleActivate(acc.email)}
                          className="text-orange-600 dark:text-orange-400 border-orange-500/30 hover:bg-orange-500/10 font-medium"
                        >
                          <MailWarning className="h-4 w-4 mr-1" /> {"激活"}
                        </Button>
                      )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleVerify(acc.email)}
                      disabled={verifying === acc.email}
                      title={"单独验证"}
                    >
                      {verifying === acc.email ? (
                        <RefreshCw className="h-4 w-4 animate-spin text-blue-500" />
                      ) : (
                        <ShieldCheck className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDisableUpdateMemory(acc.email)}
                      className="h-8 w-8 p-0"
                      title={"关闭更新记忆"}
                    >
                      <Settings2 className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDisableMemory(acc.email)}
                      className="h-8 w-8 p-0"
                      title={"关闭记忆"}
                    >
                      <Brain className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleClearMemories(acc.email)}
                      className="h-8 w-8 p-0"
                      title={"清空记忆"}
                    >
                      <Eraser className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleClearChats(acc.email)}
                      className="h-8 w-8 p-0"
                      title={"清空聊天记录"}
                    >
                      <MessageSquareX className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(acc.email)}
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      title={"删除账号"}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <BatchImportModal
        isOpen={isBatchImportOpen}
        onClose={() => setIsBatchImportOpen(false)}
        onImportComplete={fetchAccounts}
      />
      <BatchExportModal
        isOpen={isBatchExportOpen}
        onClose={() => setIsBatchExportOpen(false)}
      />
      <StrategyConfigModal
        isOpen={isStrategyConfigOpen}
        onClose={() => setIsStrategyConfigOpen(false)}
      />
    </div>
  );
}
