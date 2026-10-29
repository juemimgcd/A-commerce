import {
  DefaultResourceLoader,
  type ExtensionFactory,
  type SettingsManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Check } from "typebox/value";
import { join } from "node:path";
import { config } from "./config.ts";
import { systemPrompt } from "./prompt.ts";

const guides = {
  shopping:
    "先了解当前预算、用途、兼容性和排除条件；调用商品工具获取候选，比较可核实的规格。候选顺序和商品ID保持对应。有下一页时说明候选范围，不把本页当作全商城。",
  aftersales:
    "先确认用户指向的订单和商品，再查询退货资格或售后记录。区分申请未提交、处理中、退款处理中与已退款。实际条件以订单政策快照和后端结果为准，提供可信操作入口。",
  memory:
    "只记录用户明确表达的长期购物或服务偏好。给别人买、一次购买和负面表达不推断为长期偏好。先读取记忆开关，候选必须引用本轮原文并等待用户确认。",
};
const parameters = Type.Object(
  {
    topic: Type.Union([
      Type.Literal("shopping"),
      Type.Literal("aftersales"),
      Type.Literal("memory"),
    ]),
  },
  { additionalProperties: false },
);

export const guideTool: ToolDefinition = {
  name: "commerce_get_guide",
  label: "读取商城处理指引",
  description:
    "按需读取选购、售后或偏好处理方法。内容是固定指引，不提供当前商品、订单或商家政策事实。",
  parameters,
  async execute(_id, input) {
    if (!Check(parameters, input)) throw new Error("无效指引主题。");
    return {
      content: [{ type: "text", text: guides[input.topic] }],
      details: {},
    };
  },
};

export async function commerceResources(
  settings: SettingsManager,
  extension: ExtensionFactory,
) {
  const loader = new DefaultResourceLoader({
    cwd: config.stateDir,
    agentDir: join(config.stateDir, "resources"),
    settingsManager: settings,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    systemPrompt,
    extensionFactories: [extension],
  });
  await loader.reload();
  return loader;
}
