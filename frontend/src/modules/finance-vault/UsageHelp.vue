<template>
  <el-collapse v-model="active" class="usage-help">
    <el-collapse-item title="使用说明" name="help">
      <div class="usage-body">
        <p class="usage-title">数据池清洗是什么？</p>
        <ul>
          <li>本机内置的 DuckDB 嵌入式数据库，数据只存在这台电脑上。</li>
          <li>可以把 xlsx / csv 文件上传成可查询的表；BIP 导入等其它模块的数据也会自动入池，在这里统一清洗、整合和查询。</li>
        </ul>

        <template v-if="page === 'list'">
          <p class="usage-title">本页怎么用？</p>
          <ul>
            <li>「上传入池」：点右上角按钮选择 xlsx/csv 文件；表名只能用「小写字母、数字、下划线」（如 t_expense）。</li>
            <li>「追加」：在现有表末尾累加新数据；「整表重建」：先清空再重建，会覆盖已有数据，请谨慎使用。</li>
            <li>「预览」：查看该表的前 100 行，确认数据是否正常。</li>
            <li>「画像」：查看每列的类型推断、缺失率、唯一率和高频值，并给出清洗建议（常量列/全空列/疑似 ID）。</li>
            <li>「清洗」：按配方清掉脏数据——脏值归一、剔除空行/空列、整行去重、关键列必填、无效值剔除或置空；先预览再应用，结果写入新表，原始数据不动。</li>
            <li>「分析」：跳转到整合分析页签，按维度分组 + 指标汇总生成数据表和图表，可导出 xlsx 或另存为新数据集。</li>
            <li>「查询」：跳转到 SQL 查询页，并自动填好该表的查询语句模板。</li>
          </ul>
        </template>

        <template v-else>
          <p class="usage-title">本页怎么用？</p>
          <ul>
            <li>只允许 SELECT / WITH 开头的只读查询，不会改动仓里的数据。</li>
            <li>只能引用表清单页里已注册的表名，其它名字会被拒绝。</li>
            <li>结果最多返回 1000 行，超出会提示「已截断」，可加 LIMIT 或条件缩小范围。</li>
            <li>单条查询超过 5 秒会被自动中断，请拆分或简化语句。</li>
            <li>「导出 CSV」可下载查询结果，文件带 BOM，用 Excel 直接打开不乱码。</li>
          </ul>
        </template>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup>
import { ref } from 'vue'

// page 区分说明侧重：list=表清单页（上传/预览），query=SQL 查询页（只读约束/导出）
defineProps({
  page: {
    type: String,
    required: true,
    validator: (v) => ['list', 'query'].includes(v),
  },
})

// 默认收起：说明属辅助信息，不长期占用首屏空间
const active = ref([])
</script>

<style scoped>
.usage-help {
  margin-bottom: 12px;
}
.usage-title {
  margin: 4px 0;
  font-weight: 600;
}
.usage-body ul {
  margin: 4px 0 10px;
  padding-left: 20px;
}
.usage-body li {
  line-height: 1.8;
}
</style>
