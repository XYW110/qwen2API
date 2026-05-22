import { useState } from "react";
import { Button } from "./ui/button";
import { toast } from "sonner";
import { getAuthHeader } from "../lib/auth";
import { API_BASE } from "../lib/api";
import { Download, X, Copy, Check } from "lucide-react";

interface BatchExportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function BatchExportModal({ isOpen, onClose }: BatchExportModalProps) {
  const [exporting, setExporting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [accountsText, setAccountsText] = useState("");

  const handleExport = async () => {
    setExporting(true);
    const id = toast.loading("正在导出账号...");
    
    try {
      const response = await fetch(`${API_BASE}/api/admin/accounts/export`, {
        method: "GET",
        headers: getAuthHeader(),
      });
      
      const data = await response.json();
      
      if (data.ok) {
        setAccountsText(data.accounts_text);
        toast.success(`导出完成：共 ${data.count} 个账号`, { id });
      } else {
        toast.error(data.error || "导出失败", { id });
      }
    } catch (error) {
      toast.error("导出请求失败", { id });
    } finally {
      setExporting(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(accountsText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleClose = () => {
    setAccountsText("");
    setCopied(false);
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
            <Download className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">批量导出账号</h2>
          </div>
          <Button variant="ghost" size="sm" onClick={handleClose} className="h-8 w-8 p-0">
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* 格式说明 */}
          <div className="rounded-lg bg-muted p-3">
            <p className="text-sm font-medium mb-2">导出格式：</p>
            <ul className="text-xs text-muted-foreground space-y-1">
              <li>• <code className="bg-muted-foreground/10 px-1 rounded">email:password</code> - 邮箱+密码</li>
              <li>• <code className="bg-muted-foreground/10 px-1 rounded">email:password|proxy_url</code> - 邮箱+密码+代理</li>
              <li>• 每行一个账号</li>
            </ul>
          </div>

          {/* 导出按钮 */}
          {!accountsText && (
            <div className="text-center py-8">
              <Button 
                onClick={handleExport} 
                disabled={exporting}
                className="px-8 py-6 text-lg"
              >
                {exporting ? (
                  <>
                    <Download className="mr-2 h-5 w-5 animate-spin" />
                    导出中...
                  </>
                ) : (
                  <>
                    <Download className="mr-2 h-5 w-5" />
                    开始导出
                  </>
                )}
              </Button>
            </div>
          )}

          {/* 导出结果 */}
          {accountsText && (
            <div className="space-y-3">
              <div className="flex justify-between items-center">
                <p className="text-sm font-medium">导出的账号</p>
                <Button 
                  variant="outline" 
                  size="sm" 
                  onClick={handleCopy}
                  className="flex items-center gap-1"
                >
                  {copied ? (
                    <>
                      <Check className="h-4 w-4" />
                      已复制
                    </>
                  ) : (
                    <>
                      <Copy className="h-4 w-4" />
                      复制到剪贴板
                    </>
                  )}
                </Button>
              </div>
              <textarea
                value={accountsText}
                readOnly
                className="w-full h-64 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-none"
              />
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t">
          <Button variant="outline" onClick={handleClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  );
}