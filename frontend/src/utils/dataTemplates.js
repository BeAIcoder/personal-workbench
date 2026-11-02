// 数据模板清单与下载：文件名与后端白名单（app/routers/data_templates.py）一一对应。
// 界面各上传页通过 <TemplateLinks type="..." /> 引用，改模板只需改这里 + 后端白名单。
import { ElMessage } from 'element-plus'
import { API_BASE } from '../api'

export const DATA_TEMPLATES = {
  vault: [
    { file: '数据仓-入仓模板.xlsx', label: 'xlsx 模板' },
    { file: '数据仓-入仓模板.csv', label: 'csv 模板' },
  ],
  personal: [{ file: '个人记账-导入模板.csv', label: 'CSV 导入模板' }],
  expense: [{ file: '费用分类-银行存款辅助明细账-模板.xlsx', label: '银行存款辅助明细账模板' }],
  cashflow: [{ file: '费用分类-银行存款辅助明细账-模板.xlsx', label: '银行存款明细账模板' }],
  pos: [
    { file: 'POS对账-认领记录-模板.xlsx', label: '认领记录' },
    { file: 'POS对账-辅助明细账-模板.xlsx', label: '辅助明细账' },
    { file: 'POS对账-凭证列表-模板.xlsx', label: '凭证列表' },
  ],
  bip: [
    { file: 'BIP账簿-凭证列表-模板.xlsx', label: '凭证列表' },
    { file: 'BIP账簿-辅助明细账-模板.xlsx', label: '辅助明细账' },
    { file: 'BIP账簿-辅助余额表-模板.xlsx', label: '辅助余额表' },
  ],
}

export function templateUrl(file) {
  return `${API_BASE}/templates/${encodeURIComponent(file)}`
}

// 走 <a download> 触发浏览器下载：响应带 Content-Disposition，中文名不依赖浏览器猜名。
export function downloadTemplate(file) {
  if (!file) return
  const a = document.createElement('a')
  a.href = templateUrl(file)
  a.download = file
  document.body.appendChild(a)
  a.click()
  a.remove()
  ElMessage.success(`已开始下载：${file}`)
}
