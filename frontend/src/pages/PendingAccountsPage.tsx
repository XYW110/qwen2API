import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import {
  CheckCircle,
  XCircle,
  RefreshCw,
  Clock,
  UserCheck,
} from "lucide-react";
import { toast } from "sonner";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";

type PendingAccountItem = {
  id: string;
  email: string;
  proxy: string | null;
  cookies: string | null;
  submitted_by_api_key: string;
  created_at: number;
};

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleString("zh-CN", { hour12: false });
}

export default function PendingAccountsPage() {
  const [pendingAccounts, setPendingAccounts] = useState<PendingAccountItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchPendingAccounts = () => {
    setLoading(true);
    fetch(`${API_BASE}/api/admin/pending-accounts`, { headers: getAuthHeader() })
      .then((res) => {
        if (!res.ok) throw new Error("unauthorized");
        return res.json();
      })
      .then((data) => {
        setPendingAccounts(data.pending_accounts || []);
        setLoading(false);
      })
      .catch(() => {
        toast.error("获取待审批账户列表失败，请检查会话密钥");
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchPendingAccounts();
  }, []);

  const handleApprove = (id: string, email: string) => {
    const confirmApprove = window.confirm(`确定要批准账户 ${email} 吗？这将登录并验证该账户。`);
    if (!confirmApprove) return;

    const loadingId = toast.loading(`正在批准账户 ${email}...`);
    fetch(`${API_BASE}/api/admin/pending-accounts/${id}/approve`, {
      method: "POST",
      headers: getAuthHeader(),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.ok) {
          toast.success(`已批准账户 ${email}`, { id: loadingId });
          fetchPendingAccounts();
        } else {
          toast.error(data.error || "批准账户失败", { id: loadingId, duration: 8000 });
        }
      })
      .catch(() => {
        toast.error("批准账户请求失败", { id: loadingId });
      });
  };

  const handleReject = (id: string, email: string) => {
    const confirmReject = window.confirm(`确定要拒绝账户 ${email} 吗？此操作将删除该记录。`);
    if (!confirmReject) return;

    const loadingId = toast.loading(`正在拒绝账户 ${email}...`);
    fetch(`${API_BASE}/api/admin/pending-accounts/${id}/reject`, {
      method: "POST",
      headers: getAuthHeader(),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.ok) {
          toast.success(`已拒绝账户 ${email}`, { id: loadingId });
          fetchPendingAccounts();
        } else {
          toast.error(data.error || "拒绝账户失败", { id: loadingId, duration: 8000 });
        }
      })
      .catch(() => {
        toast.error("拒绝账户请求失败", { id: loadingId });
      });
  };

  return (
    <div className="space-y-6 relative">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-3xl font-extrabold tracking-tight flex items-center gap-3">
            <Clock className="h-8 w-8 text-primary" />
            {"待审批账户"}
          </h2>
          <p className="text-muted-foreground mt-1">
            {"管理通过公共 API 提交的待审批账户，包括登录验证和审批操作。"}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => {
              fetchPendingAccounts();
              toast.success("列表已刷新");
            }}
            disabled={loading}
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />{"刷新列表"}
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <UserCheck className="h-4 w-4 text-muted-foreground" />
            <div className="text-sm text-muted-foreground">{"待审批数量"}</div>
          </div>
          <div className="text-2xl font-bold mt-1">{pendingAccounts.length}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-muted-foreground" />
            <div className="text-sm text-muted-foreground">{"已批准"}</div>
          </div>
          <div className="text-2xl font-bold mt-1">-</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-muted-foreground" />
            <div className="text-sm text-muted-foreground">{"已拒绝"}</div>
          </div>
          <div className="text-2xl font-bold mt-1">-</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <RefreshCw className="h-4 w-4 text-muted-foreground" />
            <div className="text-sm text-muted-foreground">{"今日提交"}</div>
          </div>
          <div className="text-2xl font-bold mt-1">-</div>
        </div>
      </div>

      <div className="rounded-2xl border bg-card/30 overflow-hidden">
        <div className="flex items-center justify-between p-6 border-b bg-muted/10">
          <h3 className="text-xl font-bold">{"待审批列表"}</h3>
          <span className="inline-flex items-center justify-center bg-primary/10 text-primary rounded-full px-3 py-1 text-xs font-bold">
            {pendingAccounts.length}
          </span>
        </div>
        <table className="w-full text-sm text-left">
          <thead className="bg-muted/30 border-b text-muted-foreground text-xs uppercase tracking-wider font-semibold">
            <tr>
              <th className="h-12 px-6 align-middle">{"邮箱"}</th>
              <th className="h-12 px-6 align-middle">{"代理"}</th>
              <th className="h-12 px-6 align-middle">{"提交者 API Key"}</th>
              <th className="h-12 px-6 align-middle">{"提交时间"}</th>
              <th className="h-12 px-6 align-middle text-right">{"操作"}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {pendingAccounts.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="px-6 py-12 text-center text-muted-foreground"
                >
                  {"暂无待审批账户"}
                </td>
              </tr>
            )}
            {pendingAccounts.map((account) => (
              <tr
                key={account.id}
                className="transition-colors hover:bg-black/5 dark:hover:bg-white/5"
              >
                <td className="px-6 py-4 align-middle font-medium font-mono text-foreground/90">
                  {account.email}
                </td>
                <td
                  className="px-6 py-4 align-middle text-muted-foreground font-mono max-w-200 truncate"
                  title={account.proxy || "无代理"}
                >
                  {account.proxy || "-"}
                </td>
                <td
                  className="px-6 py-4 align-middle text-muted-foreground font-mono max-w-200 truncate"
                  title={account.submitted_by_api_key}
                >
                  {account.submitted_by_api_key}
                </td>
                <td className="px-6 py-4 align-middle text-muted-foreground font-mono">
                  {formatTime(account.created_at)}
                </td>
                <td className="px-6 py-4 align-middle text-right">
                  <div className="flex items-center justify-end gap-2 flex-wrap">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleApprove(account.id, account.email)}
                      className="text-green-600 dark:text-green-400 border-green-500/30 hover:bg-green-500/10 font-medium"
                    >
                      <CheckCircle className="h-4 w-4 mr-1" />{"批准"}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleReject(account.id, account.email)}
                      className="text-red-600 dark:text-red-400 border-red-500/30 hover:bg-red-500/10 font-medium"
                    >
                      <XCircle className="h-4 w-4 mr-1" />{"拒绝"}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}