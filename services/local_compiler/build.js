
const fs = require('fs');
const path = require('path');
const esbuild = require('./lib/main.js');

// 1. 读取输入 (从 stdin 或文件)
// Agent 将文件内容作为 JSON 字符串传递给 stdin
let inputData = '';

process.stdin.on('data', chunk => {
    inputData += chunk;
});

process.stdin.on('end', async () => {
    try {
        if (!inputData.trim()) {
            throw new Error("No input data received");
        }
        
        const { files, env_vars } = JSON.parse(inputData);
        
        // 2. 初始化 esbuild
        const collectedExternals = [];

        // 3. 配置 Virtual FS插件
        const virtualFsPlugin = {
            name: 'virtual-fs',
            setup(build) {
                build.onResolve({ filter: /.*/ }, args => {
                    // 处理入口
                    if (args.kind === 'entry-point') {
                        return { path: args.path, namespace: 'vfs' };
                    }
                    
                    // 处理绝对路径 /src/...
                    if (args.path.startsWith('/')) {
                        return { path: args.path.substring(1), namespace: 'vfs' }; 
                    }
                    
                    // 处理 @/ 路径别名
                    if (args.path.startsWith('@/')) {
                        const aliasPath = 'src/' + args.path.substring(2);
                        return { path: aliasPath, namespace: 'vfs' };
                    }

                    // 处理相对路径
                    if (args.path.startsWith('.')) {
                        const dir = path.dirname(args.importer);
                        const resolved = path.join(dir, args.path);
                        const cleanPath = resolved.startsWith('/') ? resolved.substring(1) : resolved;
                        return { path: cleanPath, namespace: 'vfs' };
                    }
                    
                    // 忽略裸模块 CSS 导入
                    if (args.path.endsWith('.css')) {
                        return { path: args.path, namespace: 'ignore-css' };
                    }
                    
                    // 其他 (npm 包等) - 标记为 external 并收集
                    collectedExternals.push(args.path);
                    return { path: args.path, external: true };
                });

                // ... (Loaders remain same)
                build.onLoad({ filter: /.*/, namespace: 'ignore-css' }, args => {
                    return { contents: '', loader: 'css' };
                });

                build.onLoad({ filter: /.*/, namespace: 'vfs' }, args => {
                    const content = files[args.path];
                    
                    if (!content) {
                        const extensions = ['.tsx', '.ts', '.jsx', '.js', '.css', '.json'];
                        for (const ext of extensions) {
                            if (files[args.path + ext]) {
                                return {
                                    contents: files[args.path + ext],
                                    loader: getLoader(args.path + ext)
                                };
                            }
                        }
                        return { errors: [{ text: `File not found in VFS: ${args.path}` }] };
                    }
                    
                    return {
                        contents: content,
                        loader: getLoader(args.path)
                    };
                });
            }
        };

        // 4. 执行编译
        const result = await esbuild.build({
            entryPoints: ['src/main.tsx'],
            bundle: true,
            format: 'esm',
            target: ['es2020'],
            outfile: 'bundle.js',
            plugins: [virtualFsPlugin],
            jsx: 'automatic', // 使用新的 JSX 转换
            write: false,
            external: ['react', 'react-dom', 'react-dom/client'],
            define: {
                'process.env.NODE_ENV': '"development"',
                ...Object.fromEntries(
                    Object.entries(env_vars || {}).map(([k, v]) => [
                        `process.env.${k}`, 
                        JSON.stringify(v)
                    ])
                )
            }
        });

        // 5. 处理结果
        let jsCode = '';
        let cssCode = '';

        for (const file of result.outputFiles) {
            if (file.path.endsWith('.js')) {
                jsCode = file.text;
            } else if (file.path.endsWith('.css')) {
                cssCode = file.text;
            }
        }

        if (cssCode) {
            const escapedCss = JSON.stringify(cssCode);
            const injectScript = `
(function() {
    try {
        const style = document.createElement('style');
        style.textContent = ${escapedCss};
        document.head.appendChild(style);
    } catch(e) {
        console.error('Failed to inject CSS:', e);
    }
})();
`;
            jsCode = injectScript + jsCode;
        }

        console.log(JSON.stringify({ 
            success: true, 
            output: jsCode,
            externals: [...new Set(collectedExternals)]
        }));

    } catch (e) {
        // 格式化错误信息
        let errorMsg = e.message;
        if (e.errors && e.errors.length > 0) {
            errorMsg = e.errors.map(err => {
                let text = err.text;
                if (err.location) {
                    text += ` at ${err.location.file}:${err.location.line}:${err.location.column}`;
                    text += `\n${err.location.lineText}\n${'^'.padStart(err.location.column + 1, ' ')}`;
                }
                return text;
            }).join('\n\n');
        }
        
        console.log(JSON.stringify({ success: false, error: errorMsg }));
        process.exit(0); // 正常退出，通过 JSON 返回错误
    }
});

function getLoader(filename) {
    const ext = path.extname(filename);
    switch (ext) {
        case '.js': return 'jsx'; // 允许 js 中写 jsx
        case '.jsx': return 'jsx';
        case '.ts': return 'ts';
        case '.tsx': return 'tsx';
        case '.css': return 'css';
        case '.json': return 'json';
        default: return 'text';
    }
}
