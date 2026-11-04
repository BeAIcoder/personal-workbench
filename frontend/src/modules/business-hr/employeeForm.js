/**
 * 人事员工表单的纯函数契约：新建默认值 / 删除确认文案。
 *
 * 新建时 social_base 必须是 null（留空 = 按基本工资），不能是 0：
 * 0 是显式「不缴社保」，接口与出薪口径都按此区分。
 */

export function emptyEmployeeForm() {
  return {
    id: null,
    emp_no: '',
    name: '',
    department: '',
    position: '',
    hire_date: '',
    probation_end: '',
    contract_end: '',
    status: 'active',
    phone: '',
    base_salary: 0,
    social_base: null,
    special_additional: 0,
    note: '',
  }
}

export function employeeDeleteConfirmText(name) {
  return `删除员工「${name}」会同时删除其考勤与草稿薪资；`
    + '有已确认薪资的员工会被拒绝删除（请改为「离职」保留历史）。确认继续？'
}
