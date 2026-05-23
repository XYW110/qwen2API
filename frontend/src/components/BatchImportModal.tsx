import { useState } from "react";
import { Button } from "./ui/button";
import { toast } from "sonner";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";
import {
  Upload,
  X,
  AlertCircle,
  CheckCircle,
  XCircle,
  SkipForward,
} from "lucide-react";

type ImportResult = {
  ok: boolean;
  total: number;
  success: number;
  failed: number;
  skipped: number;
  invalid: number;
  results: Array<{ email: string; ok: boolean; error?: string }>;
};

interface BatchImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete: () => void;
}

export function BatchImportModal({
  isOpen,
  onClose,
  onImportComplete,
}: BatchImportModalProps) {
  const [accountsText, setAccountsText] = useState("");
  const [concurrency, setConcurrency] = useState(5);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [clearMemories, setClearMemories] = useState(true);
  const [disableMemory, setDisableMemory] = useState(true);
  const [clearChats, setClearChats] = useState(false);

  const handleImport = async () => {
    if (!accountsText.trim()) {
      toast.error("请输入账号信息");
      return;
    }

    setImporting(true);
    const id = toast.loading("正在批量导入账号...");

    try {
      const response = await fetch(
        `${API_BASE}/api/admin/accounts/batch-import`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeader() },
          body: JSON.stringify({
            accounts_text: accountsText,
            concurrency,
            clear_memories: clearMemories,
            disable_memory: disableMemory,
            clear_chats: clearChats,
          }),
        }
      );

      const data = await response.json();
      setResult(data);

      if (data.ok) {
        toast.success(
          `导入完成：成功 ${data.success}，失败 ${data.failed}，跳过 ${data.skipped}，格式错误 ${data.invalid}`,
          { id }
        );
        onImportComplete();
      } else {
        toast.error(data.error || "导入失败", { id });
      }
    } catch (error) {
      toast.error("批量导入请求失败", { id });
    } finally {
      setImporting(false);
    }
  };

  const handleClose = () => {
    setAccountsText("");
    setResult(null);
    setClearMemories(true);
    setDisableMemory(true);
    setClearChats(false);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-2xl mx-4 bg-background rounded-xl border shadow-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <div className="flex items-center gap-2">
            <Upload className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">批量导入账号</h2>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClose}
            className="h-8 w-8 p-0"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* 格式说明 */}
          <div className="rounded-lg bg-muted p-3">
            <p className="text-sm font-medium mb-2">支持格式：</p>
            <ul className="text-xs text-muted-foreground space-y-1">
              <li>
                •{" "}
                <code className="bg-muted-foreground/10 px-1 rounded">
                  email:password
                </code>{" "}
                - 邮箱+密码
              </li>
              <li>
                •{" "}
                <code className="bg-muted-foreground/10 px-1 rounded">
                  email:password|proxy_url
                </code>{" "}
                - 邮箱+密码+代理
              </li>
              <li>• 每行一个账号，支持多行批量导入</li>
            </ul>
          </div>

          {/* 账号文本输入 */}
          <div>
            <label className="text-sm font-medium mb-1.5 block">账号列表</label>
            <textarea
              value={accountsText}
              onChange={(e) => setAccountsText(e.target.value)}
              placeholder={`user1@example.com:password1\nuser2@example.com:password2|https://proxy:8080\nuser3@example.com:password3`}
              className="w-full h-40 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-none"
            />
          </div>

          {/* 并发数设置 */}
          <div>
            <label className="text-sm font-medium mb-1.5 block">
              并发数：{concurrency}
            </label>
            <input
              type="range"
              min="1"
              max="20"
              value={concurrency}
              onChange={(e) => setConcurrency(parseInt(e.target.value))}
              className="w-full"
            />
            <div className="flex justify-between text-xs text-muted-foreground mt-1">
              <span>1</span>
              <span>推荐 5-10</span>
              <span>20</span>
            </div>
          </div>

          {/* 记忆配置选项 */}
          <div className="space-y-2">
            <div className="flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={clearMemories}
                  onChange={(e) => setClearMemories(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300"
                />
                <span>清空记忆</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={disableMemory}
                  onChange={(e) => setDisableMemory(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300"
                />
                <span>不再记忆</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={clearChats}
                  onChange={(e) => setClearChats(e.target.checked)}
                  className="w-4 h-4 rounded border-gray-300"
                />
                <span>清空聊天记录</span>
              </label>
            </div>
            <p className="text-xs text-muted-foreground">
              记忆配置：防止不同账户的记忆互相影响对话
            </p>
          </div>

          {/* 导入结果 */}
          {result && (
            <div className="rounded-lg border p-3 space-y-2">
              <p className="text-sm font-medium">导入结果</p>
              <div className="grid grid-cols-4 gap-2 text-center">
                <div className="rounded bg-green-500/10 p-2">
                  <CheckCircle className="h-4 w-4 mx-auto text-green-500 mb-1" />
                  <div className="text-lg font-bold">{result.success}</div>
                  <div className="text-xs text-muted-foreground">成功</div>
                </div>
                <div className="rounded bg-red-500/10 p-2">
                  <XCircle className="h-4 w-4 mx-auto text-red-500 mb-1" />
                  <div className="text-lg font-bold">{result.failed}</div>
                  <div className="text-xs text-muted-foreground">失败</div>
                </div>
                <div className="rounded bg-yellow-500/10 p-2">
                  <SkipForward className="h-4 w-4 mx-auto text-yellow-500 mb-1" />
                  <div className="text-lg font-bold">{result.skipped}</div>
                  <div className="text-xs text-muted-foreground">跳过</div>
                </div>
                <div className="rounded bg-gray-500/10 p-2">
                  <AlertCircle className="h-4 w-4 mx-auto text-gray-500 mb-1" />
                  <div className="text-lg font-bold">{result.invalid}</div>
                  <div className="text-xs text-muted-foreground">格式错误</div>
                </div>
              </div>

              {/* 失败详情 */}
              {result.failed > 0 && (
                <div className="mt-2">
                  <p className="text-xs font-medium mb-1">失败详情：</p>
                  <div className="max-h-24 overflow-y-auto text-xs space-y-1">
                    {result.results
                      .filter((r) => !r.ok)
                      .map((r, i) => (
                        <div key={i} className="text-red-500">
                          {r.email}: {r.error}
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t">
          <Button variant="outline" onClick={handleClose}>
            {result ? "关闭" : "取消"}
          </Button>
          <Button
            onClick={handleImport}
            disabled={importing || !accountsText.trim()}
          >
            {importing ? (
              <>
                <Upload className="mr-2 h-4 w-4 animate-spin" />
                导入中...
              </>
            ) : (
              <>
                <Upload className="mr-2 h-4 w-4" />
                开始导入
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
