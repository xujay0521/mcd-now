# MCP 调用契约

## 券实例

- `query-my-coupons` 只表示用户拥有，不表示当前门店可核销。
- 券实例键为 `couponId + couponCode`。
- 临期判断使用 `tradeEndDateTime`，时区固定为 `Asia/Shanghai`。
- 同时检查 `enable`、`currentDayAvailableCount`、`tradeTimePeriods`、
  `orderTypes` 和 `beTypes`；首版只为到店自取生成订单建议。

## 门店校验

1. 使用用户提供的城市和位置关键词调用 `query-nearby-stores`。
2. 排除暂停营业门店，按 `distance` 升序检查前 5 家。
3. 对每家调用 `query-store-coupons`，匹配完整券实例键。
4. 最多返回 3 个可用门店，并注明“已检查 N 家附近门店”。
5. `distance` 为 0 或缺失时，不排序或估算抵达时间。

## 菜单与核价

- 只使用 `query-meals` 或券关联结果返回的 `productCode`。
- 套餐候选必须调用 `query-meal-detail` 检查 `minQuantity` 和可替换项。
- 特调必须传完整键；存在 `unselectedKey` 时，未选项也必须传入。
- 最终应付金额只取 `calculate-price` 的结果。
- 无同款普通商品对照时，不计算或宣称准确节省金额。

## 写操作

- `auto-bind-coupons` 会领取全部可领券，调用前必须明确说明影响并获得同意。
- `create-order` 前必须展示门店、商品、数量、券、最终价格和取餐方式。
- 到店场景的 `takeWayCode` 必须来自本次 `calculate-price.takeWayList`。
- 首版只允许 `orderType=1`、`beType=1`，不得传 `beCode` 或 `addressId`。
- 确认后必须重新核价；价格、商品、券、门店或履约方式变化均需重新确认。
- CLI 和定时任务不允许连接 MCP；所有实时调用仅限用户主动发起的交互式会话。

得来速和麦乐送属于后续版本，企业团餐不在本 Skill 范围内。
