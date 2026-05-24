import { useState, useEffect } from "react"
import { Button } from "../components/ui/button"
import { RefreshCw, AlertTriangle, Search, ChevronDown, ChevronUp, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { getAuthHeader } from "../lib/auth"
import { API_BASE } from "../lib/api"

type FailureItem = {
  id: string
  api_key: string
  account_email: string
  model: string
  timestamp: number
  error_message: string
}

export default function FailuresPage() {
  const [failures, setFailures] = useState<FailureItem[]>([])
  const [filteredFailures, setFilteredFailures] = useState<FailureItem[]>([])
  const [apiKeys, setApiKeys] = useState<string[]>([])
  const [selectedKey, setSelectedKey] = useState<string>("all")
  const [searchTerm, setSearchTerm] = useState("")
  const [expandedErrors, setExpandedErrors] = useState<Set<string>>(new Set())
  const [confirmClean, setConfirmClean] = useState(false)

  const fetchFailures = () => {
    fetch(`${API_BASE}/api/admin/keys/failures`, { headers: getAuthHeader() })
      .then(res => {
        if (!res.ok) throw new Error("Unauthorized")
        return res.json()
      })
      .then(data => {
        setFailures(data.failures || [])
        // Extract unique API keys for filter dropdown
        const uniqueKeys = Array.from(new Set((data.failures || []).map((f: FailureItem) => f.api_key)))
        setApiKeys(uniqueKeys)
      })
      .catch(() => toast.error("获取失败记录失败，请检查会话 Key"))
  }

  useEffect(() => {
    fetchFailures()
  }, [])

  // Apply filters
  useEffect(() => {
    let result = failures
    
    // Filter by API key
    if (selectedKey !== "all") {
      result = result.filter(f => f.api_key === selectedKey)
    }
    
    // Filter by search term
    if (searchTerm) {
      const term = searchTerm.toLowerCase()
      result = result.filter(f => 
        f.error_message.toLowerCase().includes(term) ||
        f.account_email.toLowerCase().includes(term) ||
        f.model.toLowerCase().includes(term)
      )
    }
    
    setFilteredFailures(result)
  }, [failures, selectedKey, searchTerm])

  const handleCleanAll = () => {
    fetch(`${API_BASE}/api/admin/keys/failures/clean`, {
      method: "POST",
      headers: getAuthHeader()
    }).then(async res => {
      if (res.ok) {
        const data = await res.json().catch(() => ({}))
        toast.success(`已清理 ${data.cleaned || 0} 条记录`)
        fetchFailures()
        setConfirmClean(false)
      } else {
        toast.error("清理失败")
      }
    }).catch(() => toast.error("清理失败"))
  }

  const toggleExpandError = (id: string) => {
    const newExpanded = new Set(expandedErrors)
    if (newExpanded.has(id)) {
      newExpanded.delete(id)
    } else {
      newExpanded.add(id)
    }
    setExpandedErrors(newExpanded)
  }

  const formatDate = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleString("zh-CN")
  }

  const truncateError = (error: string, maxLength: number = 100) => {
    if (error.length <= maxLength) return error
    return error.substring(0, maxLength) + "..."
  }

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">API Key 失败记录</h2>
          <p className="text-muted-foreground">查看和管理 API Key 调用失败的记录。</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => { fetchFailures(); toast.success("已刷新"); }}>
            <RefreshCw className="mr-2 h-4 w-4" /> 刷新
          </Button>
          <Button variant="destructive" onClick={() => setConfirmClean(true)}>
            <Trash2 className="mr-2 h-4 w-4" /> 一键清理全部
          </Button>
        </div>
      </div>

      {/* Confirm Clean Dialog */}
      {confirmClean && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center">
          <div className="bg-card rounded-lg p-6 w-full max-w-md shadow-xl border">
            <div className="flex items-center gap-3 mb-4">
              <AlertTriangle className="h-6 w-6 text-destructive" />
              <h3 className="text-lg font-semibold">确认清理</h3>
            </div>
            <p className="text-muted-foreground mb-6">确定要清理所有失败记录吗？此操作不可撤销。</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setConfirmClean(false)}>取消</Button>
              <Button variant="destructive" onClick={handleCleanAll}>确认清理</Button>
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col md:flex-row gap-4">
        <div className="flex-1">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="搜索错误信息、邮箱或模型..."
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              className="pl-10 pr-4 py-2 w-full rounded-md border border-input bg-background text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>
        </div>
        <div>
          <select
            value={selectedKey}
            onChange={e => setSelectedKey(e.target.value)}
            className="h-10 px-3 py-2 rounded-md border border-input bg-background text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="all">所有 API Key</option>
            {apiKeys.map(key => (
              <option key={key} value={key}>{key}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Failures Table */}
      <div className="rounded-xl border bg-card overflow-hidden">
        <table className="w-full text-sm text-left">
          <thead className="bg-muted/50 border-b text-muted-foreground">
            <tr>
              <th className="h-12 px-4 align-middle font-medium w-16">序号</th>
              <th className="h-12 px-4 align-middle font-medium">API Key</th>
              <th className="h-12 px-4 align-middle font-medium">账户邮箱</th>
              <th className="h-12 px-4 align-middle font-medium">模型</th>
              <th className="h-12 px-4 align-middle font-medium w-40">失败时间</th>
              <th className="h-12 px-4 align-middle font-medium">错误信息</th>
            </tr>
          </thead>
          <tbody>
            {filteredFailures.length === 0 && (
              <tr>
                <td colSpan={6} className="p-4 text-center text-muted-foreground">暂无失败记录</td>
              </tr>
            )}
            {filteredFailures.map((failure, i) => (
              <tr key={failure.id} className="border-b transition-colors hover:bg-muted/50">
                <td className="p-4 align-middle font-medium text-muted-foreground">{i + 1}</td>
                <td className="p-4 align-middle font-mono text-xs">{failure.api_key}</td>
                <td className="p-4 align-middle text-xs">{failure.account_email}</td>
                <td className="p-4 align-middle text-xs">{failure.model}</td>
                <td className="p-4 align-middle text-xs text-muted-foreground">{formatDate(failure.timestamp)}</td>
                <td className="p-4 align-middle">
                  <div className="flex flex-col gap-1">
                    <div className="text-xs">
                      {expandedErrors.has(failure.id) ? failure.error_message : truncateError(failure.error_message)}
                    </div>
                    {failure.error_message.length > 100 && (
                      <button
                        onClick={() => toggleExpandError(failure.id)}
                        className="text-xs text-primary hover:underline flex items-center gap-1 w-fit"
                      >
                        {expandedErrors.has(failure.id) ? (
                          <>
                            <span>收起</span>
                            <ChevronUp className="h-3 w-3" />
                          </>
                        ) : (
                          <>
                            <span>展开</span>
                            <ChevronDown className="h-3 w-3" />
                          </>
                        )}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}