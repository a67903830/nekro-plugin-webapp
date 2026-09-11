// HTML 模块类型声明（配合 wrangler 的 Text 规则使用）
declare module '*.html' {
	const content: string;
	export default content;
}
