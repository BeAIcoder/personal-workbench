// 技能包页的 URL 编码：包身份是目录名（可由磁盘目录带出，不限于新建时的名称规则），
// 目录名里的 # ? % 空格等直接拼进 URL 会被浏览器截断/改写，请求落到错误的 API 路径
// （例如 tax#1 的 /packs/tax#1/enabled 实际请求 /packs/tax/enabled），必须逐段编码。

// 包目录名 → 单个 URL 路径段
export function encodePackName(name) {
  return encodeURIComponent(name == null ? '' : String(name))
}

// 包内文件相对路径 → URL 路径（保留 / 分隔，逐段编码）
export function encodePackFilePath(filename) {
  return String(filename == null ? '' : filename)
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
}
