/**
 * Cloudflare Worker 主入口
 */

import { Env, CreatePageRequest, CreatePageResponse } from './types';
import { validateApiKey, hasPermission, generateApiKey } from './auth';
import { createPage, getPage, incrementAccessCount, deletePage, listPages, createApiKey, listApiKeys, deleteApiKey, getStats, getAdminKey, setAdminKey, getSetting, setSetting } from './storage';
import { ensureDatabaseInitialized } from './init';

// 导入欢迎页面 HTML（通过 wrangler 的 Text 规则作为模块导入）
import WELCOME_PAGE_HTML from '../public/welcome.html';

/**
 * CORS 响应头
 */
function corsHeaders() {
	return {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
		'Access-Control-Allow-Headers': 'Content-Type, Authorization',
	};
}

/**
 * JSON 响应
 */
function jsonResponse(data: any, status: number = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: {
			'Content-Type': 'application/json',
			...corsHeaders(),
		},
	});
}

/**
 * 错误响应
 */
function errorResponse(message: string, status: number = 400): Response {
	return jsonResponse({ error: message }, status);
}

/**
 * HTML 响应
 */
function htmlResponse(html: string): Response {
	return new Response(html, {
		headers: {
			'Content-Type': 'text/html; charset=utf-8',
			...corsHeaders(),
		},
	});
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		// 确保数据库已初始化（首次访问时自动初始化）
		try {
			await ensureDatabaseInitialized(env);
		} catch (error: any) {
			console.error('Database initialization failed:', error);
			return errorResponse('Database initialization failed: ' + error.message, 500);
		}

		const url = new URL(request.url);
		const path = url.pathname;

		// OPTIONS 请求处理（CORS 预检）
		if (request.method === 'OPTIONS') {
			return new Response(null, { headers: corsHeaders() });
		}

		// API 路由
		if (path.startsWith('/api/')) {
			return handleAPI(request, env, path);
		}

		// 管理路由
		if (path.startsWith('/admin/')) {
			return handleAdmin(request, env, path);
		}

		// 根路径 - 返回简单说明页
		if (path === '/' || path === '/index.html') {
			return htmlResponse(WELCOME_PAGE_HTML);
		}

		// 页面访问 /{page_id}
		const pageId = path.substring(1);
		if (pageId) {
			return servePage(pageId, env);
		}

		return errorResponse('Not Found', 404);
	},
};

/**
 * 处理 API 请求
 */
async function handleAPI(request: Request, env: Env, path: string): Promise<Response> {
	// 健康检查
	if (path === '/api/health') {
		const adminKey = await getAdminKey(env);
		return jsonResponse({ 
			status: 'healthy', 
			timestamp: Date.now(),
			initialized: !!adminKey
		});
	}

	// 初始化管理密钥（仅在未设置时可用）
	if (path === '/api/init' && request.method === 'POST') {
		try {
			const adminKey = await getAdminKey(env);
			if (adminKey) {
				return errorResponse('管理密钥已设置，无法重复初始化', 400);
			}

			const body = (await request.json()) as { admin_key: string };
			if (!body.admin_key || body.admin_key.trim().length < 8) {
				return errorResponse('管理密钥至少需要 8 个字符', 400);
			}

			await setAdminKey(body.admin_key.trim(), env);
			return jsonResponse({ message: '管理密钥设置成功' }, 201);
		} catch (e: any) {
			return errorResponse(e.message || '初始化失败', 500);
		}
	}

	// 验证 API 密钥
	const auth = await validateApiKey(request.headers.get('Authorization'), env);
	if (!auth.valid) {
		return errorResponse('Unauthorized', 401);
	}

	// POST /api/pages - 创建页面
	if (path === '/api/pages' && request.method === 'POST') {
		if (!hasPermission(auth.permissions!, 'create')) {
			return errorResponse('Forbidden', 403);
		}

		try {
			const body = (await request.json()) as CreatePageRequest;

			// 验证必填字段
			if (!body.title || !body.title.trim()) {
				return errorResponse('标题不能为空');
			}
			if (!body.description || !body.description.trim()) {
				return errorResponse('描述不能为空');
			}
			if (!body.html_content || !body.html_content.trim()) {
				return errorResponse('HTML 内容不能为空');
			}

			// 获取配置的大小限制
			const maxHtmlSizeStr = (await getSetting('max_html_size', env)) || '500';
			const maxHtmlSize = parseInt(maxHtmlSizeStr) * 1024; // KB 转字节
			if (body.html_content.length > maxHtmlSize) {
				return errorResponse(`HTML 内容过大，最大允许 ${maxHtmlSizeStr} KB`);
			}

			// 如果未指定过期天数，使用配置的默认值
			if (!body.expires_in_days) {
				const defaultExpireDays = (await getSetting('page_expire_days', env)) || '30';
				body.expires_in_days = parseInt(defaultExpireDays);
			}

			const page = await createPage(body, auth.keyId!, env);

			const response: CreatePageResponse = {
				page_id: page.page_id,
				url: `${new URL(request.url).origin}/${page.page_id}`,
				title: page.title,
				created_at: page.created_at,
				expires_at: page.expires_at,
			};

			return jsonResponse(response, 201);
		} catch (e: any) {
			return errorResponse(e.message || '创建页面失败', 500);
		}
	}

	// GET /api/pages/{id} - 获取页面信息
	const pageIdMatch = path.match(/^\/api\/pages\/([a-zA-Z0-9-]+)$/);
	if (pageIdMatch && request.method === 'GET') {
		const pageId = pageIdMatch[1];
		const page = await getPage(pageId, env);

		if (!page) {
			return errorResponse('页面不存在或已过期', 404);
		}

		// 不返回 HTML 内容，只返回元数据
		return jsonResponse({
			page_id: page.page_id,
			title: page.title,
			description: page.description,
			created_at: page.created_at,
			expires_at: page.expires_at,
			access_count: page.access_count,
		});
	}

	// DELETE /api/pages/{id} - 删除页面
	if (pageIdMatch && request.method === 'DELETE') {
		if (!hasPermission(auth.permissions!, 'delete')) {
			return errorResponse('Forbidden', 403);
		}

		const pageId = pageIdMatch[1];
		const success = await deletePage(pageId, env);

		if (!success) {
			return errorResponse('删除失败', 500);
		}

		return jsonResponse({ message: '删除成功' });
	}

	return errorResponse('Not Found', 404);
}

/**
 * 处理管理请求
 */
async function handleAdmin(request: Request, env: Env, path: string): Promise<Response> {
	// 验证管理员权限
	const auth = await validateApiKey(request.headers.get('Authorization'), env);
	if (!auth.valid || !hasPermission(auth.permissions!, 'manage')) {
		return errorResponse('Unauthorized', 401);
	}

	// GET /admin/pages - 列出所有页面
	if (path === '/admin/pages' && request.method === 'GET') {
		const pages = await listPages(env);
		return jsonResponse(pages);
	}

	// POST /admin/keys - 创建新密钥
	if (path === '/admin/keys' && request.method === 'POST') {
		try {
			const body = (await request.json()) as { key_name: string };

			if (!body.key_name || !body.key_name.trim()) {
				return errorResponse('密钥名称不能为空');
			}

		// 生成新密钥（简化版 - 直接明文）
		const newKey = generateApiKey();

		const apiKey = await createApiKey(body.key_name, newKey, auth.keyId!, env);

		// 返回密钥（明文存储，随时可见）
		return jsonResponse(apiKey, 201);
		} catch (e: any) {
			return errorResponse(e.message || '创建密钥失败', 500);
		}
	}

	// GET /admin/keys - 列出所有密钥
	if (path === '/admin/keys' && request.method === 'GET') {
		const keys = await listApiKeys(env);
		return jsonResponse(keys);
	}

	// DELETE /admin/keys/{id} - 删除密钥
	const keyIdMatch = path.match(/^\/admin\/keys\/([a-zA-Z0-9-]+)$/);
	if (keyIdMatch && request.method === 'DELETE') {
		const keyId = keyIdMatch[1];
		const success = await deleteApiKey(keyId, env);

		if (!success) {
			return errorResponse('删除失败', 500);
		}

		return jsonResponse({ message: '删除成功' });
	}

	// DELETE /admin/pages/{id} - 删除页面
	const pageIdMatch = path.match(/^\/admin\/pages\/([a-zA-Z0-9-]+)$/);
	if (pageIdMatch && request.method === 'DELETE') {
		const pageId = pageIdMatch[1];
		const success = await deletePage(pageId, env);

		if (!success) {
			return errorResponse('删除失败', 500);
		}

		return jsonResponse({ message: '删除成功' });
	}

	// GET /admin/stats - 获取统计信息
	if (path === '/admin/stats' && request.method === 'GET') {
		const stats = await getStats(env);
		return jsonResponse(stats);
	}

	// PUT /admin/password - 修改管理密钥（需要提供旧密钥）
	if (path === '/admin/password' && request.method === 'PUT') {
		try {
			const body = (await request.json()) as { old_key: string; new_key: string };
			
			if (!body.old_key || !body.new_key) {
				return errorResponse('旧密钥和新密钥都不能为空', 400);
			}

			if (body.new_key.trim().length < 8) {
				return errorResponse('新密钥至少需要 8 个字符', 400);
			}

			// 验证旧密钥（这里 auth 已经是管理员）
			const currentAdminKey = await getAdminKey(env);
			if (currentAdminKey !== body.old_key.trim()) {
				return errorResponse('旧密钥不正确', 403);
			}

			await setAdminKey(body.new_key.trim(), env);
			return jsonResponse({ message: '管理密钥修改成功' });
		} catch (e: any) {
			return errorResponse(e.message || '修改失败', 500);
		}
	}

	// GET /admin/settings - 获取系统配置
	if (path === '/admin/settings' && request.method === 'GET') {
		const pageExpireDays = (await getSetting('page_expire_days', env)) || '30';
		const maxHtmlSize = (await getSetting('max_html_size', env)) || '500';
		
		return jsonResponse({
			page_expire_days: parseInt(pageExpireDays),
			max_html_size: parseInt(maxHtmlSize),
		});
	}

	// PUT /admin/settings - 更新系统配置
	if (path === '/admin/settings' && request.method === 'PUT') {
		try {
			const body = (await request.json()) as { page_expire_days?: number; max_html_size?: number };
			
			if (body.page_expire_days !== undefined) {
				if (body.page_expire_days < 0) {
					return errorResponse('页面过期天数不能为负数', 400);
				}
				await setSetting('page_expire_days', String(body.page_expire_days), env);
			}
			
			if (body.max_html_size !== undefined) {
				if (body.max_html_size <= 0) {
					return errorResponse('HTML 最大大小必须大于 0', 400);
				}
				await setSetting('max_html_size', String(body.max_html_size), env);
			}
			
			return jsonResponse({ message: '配置更新成功' });
		} catch (e: any) {
			return errorResponse(e.message || '更新失败', 500);
		}
	}

	return errorResponse('Not Found', 404);
}

/**
 * 提供页面服务
 */
async function servePage(pageId: string, env: Env): Promise<Response> {
	const page = await getPage(pageId, env);

	if (!page) {
		return htmlResponse(`
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>页面不存在</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .error {
            text-align: center;
            padding: 2rem;
        }
        h1 {
            font-size: 6em;
            margin: 0;
            font-weight: 700;
        }
        p {
            font-size: 1.5em;
            margin-top: 1rem;
        }
    </style>
</head>
<body>
    <div class="error">
        <h1>404</h1>
        <p>页面不存在或已过期</p>
    </div>
</body>
</html>
        `);
	}

	// 更新访问计数
	await incrementAccessCount(pageId, env);

	return htmlResponse(page.html_content);
}

// 欢迎页面 HTML 已移至 public/welcome.html
// 通过 import 导入，见文件顶部
