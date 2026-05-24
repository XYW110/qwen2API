import { useState, useEffect } from "react";
import { Button } from "../components/ui/button";
import {
  Plus,
  RefreshCw,
  Copy,
  Check,
  Trash2,
  BarChart3,
  Pencil,
  X,
  ChevronUp,
} from "lucide-react";
import { toast } from "sonner";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";

type KeyItem = {
  key: string;
  note?: string;
  created_at: number;
};

type KeyStats = {
  api_key: string;
  model: string;
  request_count: number;
  total_tokens: number;
};

export default function TokensPage() {
  const [keys, setKeys] = useState<KeyItem[]>([]);
  const [copied, setCopied] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const [noteValue, setNoteValue] = useState("");
  const [statsKey, setStatsKey] = useState<string | null>(null);
  const [statsData, setStatsData] = useState<KeyStats[]>([]);
  const [showGenerateDialog, setShowGenerateDialog] = useState(false);
  const [generateNote, setGenerateNote] = useState("");

  const fetchKeys = () => {
    fetch(`${API_BASE}/api/admin/keys`, { headers: getAuthHeader() })
      .then((res) => {
        if (!res.ok) throw new Error("Unauthorized");
        return res.json();
      })
      .then((data) => setKeys(data.keys || []))
      .catch(() => toast.error("刷新失败，请检查会话 Key"));
  };

  useEffect(() => {
    fetchKeys();
  }, []);

  const handleGenerate = (note?: string) => {
    fetch(`${API_BASE}/api/admin/keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeader() },
      body: JSON.stringify({ note }),
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          toast.success("已生成新的 API Key");
          if (data.key) copyToClipboard(data.key);
          fetchKeys();
          setShowGenerateDialog(false);
          setGenerateNote("");
        } else {
          toast.error(data.detail || "生成失败，请检查权限");
        }
      })
      .catch(() => toast.error("生成失败，请检查权限"));
  };

  const handleDelete = (key: string) => {
    fetch(`${API_BASE}/api/admin/keys/${encodeURIComponent(key)}`, {
      method: "DELETE",
      headers: getAuthHeader(),
    })
      .then(async (res) => {
        if (res.ok) {
          toast.success("API Key 已删除");
          fetchKeys();
        } else {
          const data = await res.json().catch(() => ({}));
          toast.error(data.detail || "删除失败");
        }
      })
      .catch(() => toast.error("删除失败"));
  };

  const handleUpdateNote = (key: string, note?: string) => {
    fetch(`${API_BASE}/api/admin/keys/${encodeURIComponent(key)}/note`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...getAuthHeader() },
      body: JSON.stringify({ note }),
    })
      .then(async (res) => {
        if (res.ok) {
          toast.success("备注已更新");
          fetchKeys();
          setEditingNote(null);
          setNoteValue("");
        } else {
          const data = await res.json().catch(() => ({}));
          toast.error(data.detail || "更新失败");
        }
      })
      .catch(() => toast.error("更新失败"));
  };

  const handleShowStats = (key: string) => {
    if (statsKey === key) {
      setStatsKey(null);
      setStatsData([]);
      return;
    }
    fetch(`${API_BASE}/api/admin/keys/stats/${encodeURIComponent(key)}`, {
      headers: getAuthHeader(),
    })
      .then((res) => {
        if (!res.ok) throw new Error("Failed");
        return res.json();
      })
      .then((data) => {
        setStatsKey(key);
        setStatsData(data.stats || []);
      })
      .catch(() => toast.error("获取统计失败"));
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 2000);
  };

  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleString("zh-CN");
  };

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">API Key 分发</h2>
          <p className="text-muted-foreground">
            管理可以访问此网关的下游凭证。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => {
              fetchKeys();
              toast.success("已刷新");
            }}
          >
            <RefreshCw className="mr-2 h-4 w-4" /> 刷新
          </Button>
          <Button onClick={() => setShowGenerateDialog(true)}>
            <Plus className="mr-2 h-4 w-4" /> 生成新 Key
          </Button>
        </div>
      </div>

      {/* Generate Key Dialog */}
      {showGenerateDialog && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center">
          <div className="bg-card rounded-lg p-6 w-full max-w-md shadow-xl border">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-semibold">生成新的 API Key</h3>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowGenerateDialog(false)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="text-sm font-medium mb-2 block">
                  备注（可选）
                </label>
                <input
                  type="text"
                  value={generateNote}
                  onChange={(e) => setGenerateNote(e.target.value)}
                  placeholder="输入备注信息"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => setShowGenerateDialog(false)}
                >
                  取消
                </Button>
                <Button
                  onClick={() => handleGenerate(generateNote || undefined)}
                >
                  生成
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="rounded-xl border bg-card overflow-hidden">
        <table className="w-full text-sm text-left">
          <thead className="bg-muted/50 border-b text-muted-foreground">
            <tr>
              <th className="h-12 px-4 align-middle font-medium w-16">序号</th>
              <th className="h-12 px-4 align-middle font-medium">API Key</th>
              <th className="h-12 px-4 align-middle font-medium w-48">备注</th>
              <th className="h-12 px-4 align-middle font-medium w-40">
                创建时间
              </th>
              <th className="h-12 px-4 align-middle font-medium text-right w-32">
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  className="p-4 text-center text-muted-foreground"
                >
                  暂无 API Key
                </td>
              </tr>
            )}
            {keys.map((item, i) => (
              <>
                <tr
                  key={item.key}
                  className="border-b transition-colors hover:bg-muted/50"
                >
                  <td className="p-4 align-middle font-medium text-muted-foreground">
                    {i + 1}
                  </td>
                  <td className="p-4 align-middle font-mono text-xs">
                    {item.key}
                  </td>
                  <td className="p-4 align-middle">
                    {editingNote === item.key ? (
                      <div className="flex gap-1 items-center">
                        <input
                          type="text"
                          value={noteValue}
                          onChange={(e) => setNoteValue(e.target.value)}
                          className="flex h-9 w-full min-w-[10rem] rounded-md border border-input bg-background px-3 py-1.5 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          autoFocus
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            handleUpdateNote(item.key, noteValue || undefined)
                          }
                        >
                          <Check className="h-3 w-3 text-green-600" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setEditingNote(null);
                            setNoteValue("");
                          }}
                        >
                          <X className="h-3 w-3" />
                        </Button>
                      </div>
                    ) : (
                      <div
                        className="flex items-center gap-1 cursor-pointer hover:text-primary transition-colors"
                        onClick={() => {
                          setEditingNote(item.key);
                          setNoteValue(item.note || "");
                        }}
                      >
                        <span className="text-xs">{item.note || "-"}</span>
                        <Pencil className="h-3 w-3 opacity-50" />
                      </div>
                    )}
                  </td>
                  <td className="p-4 align-middle text-xs text-muted-foreground">
                    {formatDate(item.created_at)}
                  </td>
                  <td className="p-4 align-middle text-right space-x-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleShowStats(item.key)}
                      title="查看统计"
                    >
                      {statsKey === item.key ? (
                        <ChevronUp className="h-4 w-4" />
                      ) : (
                        <BarChart3 className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => copyToClipboard(item.key)}
                    >
                      {copied === item.key ? (
                        <Check className="h-4 w-4 text-green-600" />
                      ) : (
                        <Copy className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(item.key)}
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
                {/* Stats Row */}
                {statsKey === item.key && (
                  <tr className="bg-muted/30 border-b">
                    <td colSpan={5} className="p-4">
                      <div className="text-xs">
                        <div className="font-medium mb-2">
                          使用统计（按模型聚合）
                        </div>
                        {statsData.length === 0 ? (
                          <div className="text-muted-foreground">
                            暂无统计数据
                          </div>
                        ) : (
                          <div className="grid gap-2">
                            {statsData.map((stat, idx) => (
                              <div
                                key={idx}
                                className="flex items-center gap-4 bg-background/50 rounded px-3 py-2"
                              >
                                <span className="font-medium">
                                  {stat.model}
                                </span>
                                <span className="text-muted-foreground">
                                  请求: {stat.request_count}
                                </span>
                                <span className="text-muted-foreground">
                                  Tokens: {stat.total_tokens}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
