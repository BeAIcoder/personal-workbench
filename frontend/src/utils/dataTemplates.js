// 数据模板清单与下载：文件名与后端白名单（app/routers/data_templates.py）一一对应。
// 界面各上传页通过 <TemplateLinks type="..." /> 引用，改模板只需改这里 + 后端白名单。
import { ElMessage } from 'element-plus'
import api from '../api'
import { saveBlob } from './download'

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
  lease: [{ file: '租赁核算-付款计划-模板.xlsx', label: '付款计划模板' }],
  manage: [
    { file: '经营管理-内控管理表格-模板.xlsx', label: '内控管理表格' },
    { file: '经营管理-云南项目周报-模板.xlsx', label: '云南项目周报' },
    { file: '经营管理-曲靖项目周报-模板.xlsx', label: '曲靖项目周报' },
    { file: '经营管理-月度工作情况表-模板.xlsx', label: '月度工作情况表' },
  ],
}

// axios 的 baseURL 已是 API_BASE：请求必须传相对路径，否则会拼成 /api/api/templates。
export function templatePath(file) {
  return `/templates/${encodeURIComponent(file)}`
}

// 下载以服务端响应为准：先取 blob，确认成功后才落文件并提示「已下载」。
// 旧写法先弹「已开始下载」再点 <a download>，404/500 也会假成功；
// 中文名仍由前端显式给出（Content-Disposition 经代理链路不可靠）。
export async function downloadTemplate(file, deps = {}) {
  if (!file) return false
  const request = deps.request ?? api
  const save = deps.save ?? saveBlob
  const notify = deps.notify ?? ElMessage
  try {
    const { data } = await request.get(templatePath(file), { responseType: 'blob' })
    save(data, file)
    notify.success(`已下载：${file}`)
    return true
  } catch {
    // 拦截器已统一提示失败原因（Blob 错误体也会解析出 detail）
    return false
  }
}
