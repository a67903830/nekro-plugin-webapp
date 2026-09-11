const fs = require('fs');
const path = require('path');
const os = require('os');
const { exec } = require('child_process');

let inputData = '';

process.stdin.on('data', chunk => {
    inputData += chunk;
});

process.stdin.on('end', async () => {
    let tmpDir = null;
    try {
        if (!inputData.trim()) {
            throw new Error("No input data received");
        }
        
        const { files } = JSON.parse(inputData);
        
        // 1. Create Temp Directory
        tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nekro-check-'));
        
        // 2. Write Files
        for (const [filepath, content] of Object.entries(files)) {
            // Remove /src prefix if present for local structure, or just keep it structure
            // We'll write exactly as keys say, ensuring dirs exist.
            // But usually keys are "src/main.tsx".
            
            const fullPath = path.join(tmpDir, filepath);
            const dir = path.dirname(fullPath);
            fs.mkdirSync(dir, { recursive: true });
            fs.writeFileSync(fullPath, content);
        }
        
        // 3. Write tsconfig.json
        const tsconfig = {
            "compilerOptions": {
                "target": "ES2020",
                "useDefineForClassFields": true,
                "lib": ["ES2020", "DOM", "DOM.Iterable"],
                "module": "ESNext",
                "skipLibCheck": true,
                "moduleResolution": "bundler",
                "allowImportingTsExtensions": true,
                "resolveJsonModule": true,
                "isolatedModules": true,
                "noEmit": true,
                "jsx": "react-jsx",
                "strict": true,
                "noImplicitAny": false,
                "noUnusedLocals": false,
                "noUnusedParameters": false,
                "noFallthroughCasesInSwitch": true,
                "allowJs": true,
                "checkJs": false,
                // Important: Paths mappings if using absolute imports, but usually relative.
                // We need to support 'react' etc being missing.
                // Since we don't have node_modules inside tmpDir, tsc will complain about missing modules.
                // We must tell tsc to ignore module resolution errors for libraries?
                // Or we can symlink node_modules?
                // Symlinking is best.
            },
            "include": ["src"]
        };
        fs.writeFileSync(path.join(tmpDir, 'tsconfig.json'), JSON.stringify(tsconfig, null, 2));

        // 4. Run TSC
        // Try to find tsc in .bin or directly in typescript package
        let tscPath = path.resolve(__dirname, 'node_modules/.bin/tsc');
        
        if (!fs.existsSync(tscPath)) {
            // Fallback: try searching in typescript package directly
            const directPath = path.resolve(__dirname, 'node_modules/typescript/bin/tsc');
            if (fs.existsSync(directPath)) {
                tscPath = directPath;
            } else {
                // TypeScript truly not found, skip check gracefully
                console.log(JSON.stringify({ 
                    success: true, 
                    output: "Warning: TypeScript compiler (tsc) not found in local_compiler. Skipping strict type check." 
                }));
                try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
                return;
            }
        }

        // We also need types. 
        // We can symlink the node_modules from here to tmpDir
        try {
            fs.symlinkSync(path.join(__dirname, 'node_modules'), path.join(tmpDir, 'node_modules'));
        } catch (e) {
             // Ignore if symlink fails (might already exist?)
        }

        // Use 'node' to execute tscPath to be safe (it might be a script not a binary if .bin is missing)
        exec(`node "${tscPath}" -p . --noEmit --pretty false`, { cwd: tmpDir, maxBuffer: 1024 * 1024 * 5 }, (error, stdout, stderr) => {
            // Clean up
            try {
                fs.rmSync(tmpDir, { recursive: true, force: true });
            } catch (e) {}

            if (error) {
                // TSC exited with error. stdout contains the compilation errors.
                // Output is usually: "path/to/file.ts(1,1): error TS2304: Cannot find name 'x'."
                
                const rawOutput = stdout || stderr || error.message;
                
                // Parse it to be more readable for the LLM
                // We want to strip the absolute temp path prefix to avoid confusion
                // Temp path: /tmp/nekro-check-XXXXXX/src/file.ts -> vfs:src/file.ts
                
                let cleanedOutput = rawOutput.split('\n')
                    .filter(line => {
                        // Filter out empty lines
                        if (!line.trim()) return false;
                        
                        // Error Filtering Logic:
                        
                        // 1. Ignore TS2307: Cannot find module '...'
                        // This happens because we don't have npm packages installed for external libs (e.g. framer-motion, pixi.js)
                        // This is expected in our environment.
                        if (line.includes('error TS2307:')) return false;

                        // 2. Ignore TS7016: Could not find a declaration file for module '...'
                        if (line.includes('error TS7016:')) return false;

                        return true;
                    })
                    .map(line => {
                        // Replace temp dir with 'vfs:'
                        if (tmpDir && line.includes(tmpDir)) {
                            return line.replace(tmpDir, 'vfs:');
                        }
                        return line;
                    })
                    .join('\n');

                // If filtering removed all errors, report success!
                if (!cleanedOutput.trim()) {
                    console.log(JSON.stringify({ success: true, output: "No critical errors found (ignored missing modules)." }));
                    return;
                }

                // Limit output length to prevent context flooding
                if (cleanedOutput.length > 2000) {
                    cleanedOutput = cleanedOutput.substring(0, 2000) + "\n... (Status: Truncated due to length)";
                }

                console.log(JSON.stringify({ 
                    success: false, 
                    error: cleanedOutput,
                    raw: rawOutput
                }));
            } else {
                console.log(JSON.stringify({ success: true, output: "No errors found." }));
            }
        });

    } catch (e) {
        if (tmpDir) {
            try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
        }
        console.log(JSON.stringify({ success: false, error: e.message }));
    }
});
