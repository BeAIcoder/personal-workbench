# finance_bankflow 模板骨架说明

`template_skeleton.xlsx` 是生成 BIP「银行对账单」导入模板的格式基底，不是业务数据：

- 来源：用户提供的官方样例 xlsx，清空第 10 行起的业务数据行后保存；保留全部
  表头样式、列宽、数据验证下拉（币种 / 收付方向）、文本格式 `@`、字体与
  4 个辅助 sheet（ReadMe / importDropDownContent / 参照 / importTemplateInfo）。
- 内含的官方示例行（第 9 行及 ReadMe 示例）均为官方样例自带的假数据，不含
  真实账号、金额或单位信息；新增数据行从第 10 行开始追加，并复制第 9 行的
  单元格样式。
- 该文件缺失时服务抛中文 400 并拒绝生成模板（不降级为无格式模板——格式丢失
  会导致 BIP 导入端解析失败）。

账号映射配置见 `config.local.example.yaml`（复制为 `config.local.yaml` 后填写，
真实映射不入库）。
