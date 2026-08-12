const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

const DURATION_MIN = parseInt(process.env.DURATION) || 3;
const INTERVAL = 2000;

// Multiple targets to cross-validate — all HTTP-based
const TARGETS = [
    { name: 'Baidu',      url: 'https://www.baidu.com' },
    { name: 'Aliyun',     url: 'https://www.aliyun.com' },
    { name: 'Bilibili',   url: 'https://www.bilibili.com' },
];

const LOG_FILE = path.join(__dirname, 'disconnect.log');
const startTime = Date.now();

let disconnects = [];      // [{start, end, duration, targets}]
let currentDisconnect = null;

function ts() { return new Date().toISOString(); }

function log(msg) {
    const line = `[${ts()}] ${msg}`;
    console.log(line);
    fs.appendFileSync(LOG_FILE, line + '\n');
}

function checkUrl(target) {
    return new Promise((resolve) => {
        const lib = target.url.startsWith('https') ? https : http;
        const req = lib.get(target.url, { timeout: 3000 }, (res) => {
            res.resume();
            res.on('end', () => resolve(true));
            res.on('error', () => resolve(false));
            resolve(true); // got response, even if error code
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
        req.setTimeout(3000);
    });
}

async function checkAll() {
    const results = await Promise.all(TARGETS.map(async (t) => {
        const ok = await checkUrl(t);
        return { name: t.name, ok };
    }));

    const failedTargets = results.filter(r => !r.ok);
    const failCount = failedTargets.length;

    if (failCount >= 2) {
        // 2+ targets down = real disconnect
        if (!currentDisconnect) {
            currentDisconnect = {
                start: Date.now(),
                targets: failedTargets.map(r => r.name).join(', ')
            };
            log(`✖ OFFLINE — ${failCount}/${TARGETS.length} targets unreachable (${currentDisconnect.targets})`);
        }
    } else if (failCount === 1) {
        // 1 target down = that server issue, not our network
        if (currentDisconnect) {
            currentDisconnect.end = Date.now();
            currentDisconnect.duration = Math.round((currentDisconnect.end - currentDisconnect.start) / 100) / 10;
            disconnects.push(currentDisconnect);
            log(`✔ RECOVERED — offline ${currentDisconnect.duration}s`);
            currentDisconnect = null;
        }
        // silent, not a real disconnect
    } else {
        // all targets OK
        if (currentDisconnect) {
            currentDisconnect.end = Date.now();
            currentDisconnect.duration = Math.round((currentDisconnect.end - currentDisconnect.start) / 100) / 10;
            disconnects.push(currentDisconnect);
            log(`✔ RECOVERED — offline ${currentDisconnect.duration}s`);
            currentDisconnect = null;
        }
    }
}

function httpGet(url) {
    return new Promise((resolve) => {
        http.get(url, (res) => {
            let data = '';
            res.on('data', c => data += c);
            res.on('end', () => resolve(data));
        }).on('error', () => resolve(null));
    });
}

async function finalReport() {
    if (currentDisconnect) {
        currentDisconnect.end = Date.now();
        currentDisconnect.duration = Math.round((currentDisconnect.end - currentDisconnect.start) / 100) / 10;
        disconnects.push(currentDisconnect);
    }

    const totalSeconds = (Date.now() - startTime) / 1000;
    const offlineSeconds = disconnects.reduce((s, d) => s + d.duration, 0);
    const availability = ((totalSeconds - offlineSeconds) / totalSeconds * 100).toFixed(2);
    const maxDisconnect = disconnects.length ? Math.max(...disconnects.map(d => d.duration)) : 0;
    const avgDisconnect = disconnects.length ? (offlineSeconds / disconnects.length).toFixed(1) : 0;

    // MySpeed
    const speedtests = await httpGet('http://localhost:5216/api/speedtests');
    let speeds = [];
    try { speeds = JSON.parse(speedtests).slice(0, 3); } catch {}

    const divider = '='.repeat(55);

    console.log(`\n${divider}`);
    console.log(`  网络连通性检测报告`);
    console.log(`${divider}`);
    console.log(`  检测方式      : HTTP 请求 (3 个目标站点)`);
    console.log(`  目标站点      : ${TARGETS.map(t => t.name).join(', ')}`);
    console.log(`  监控时长      : ${DURATION_MIN} 分钟`);
    console.log(`  开始时间      : ${new Date(startTime).toLocaleString('zh-CN')}`);
    console.log(`  结束时间      : ${new Date().toLocaleString('zh-CN')}`);
    console.log(`${divider}`);
    console.log(`  断连次数      : ${disconnects.length} 次`);
    console.log(`  最长断连      : ${maxDisconnect} 秒`);
    console.log(`  平均断连      : ${avgDisconnect} 秒`);
    console.log(`  累计断连      : ${offlineSeconds} 秒`);
    console.log(`  网络可用率    : ${availability}%`);
    if (disconnects.length > 0) {
        console.log(`${divider}`);
        console.log(`  断连明细:`);
        disconnects.forEach((d, i) => {
            console.log(`    #${i + 1}  ${new Date(d.start).toLocaleTimeString('zh-CN')} — ${d.duration}s (不可达: ${d.targets})`);
        });
    }
    console.log(`${divider}`);

    if (speeds.length > 0) {
        console.log(`  MySpeed 测速 (同时段):`);
        speeds.forEach(s => {
            console.log(`    ${s.created.slice(11,19)}  Ping:${s.ping}ms  ↓${s.download}Mbps  ↑${s.upload}Mbps`);
        });
        console.log(`${divider}`);
    }

    if (availability >= 99.9) {
        console.log(`  结论: ✅ 网络状态优异，无异常断连。`);
    } else if (availability >= 99.0) {
        console.log(`  结论: ⚠️ 基本稳定，偶有短暂波动。`);
    } else if (availability >= 95.0) {
        console.log(`  结论: ⚠️ 存在明显断连问题，怀疑 WiFi 干扰或信道拥堵。`);
    } else {
        console.log(`  结论: ❌ 网络极不稳定，请检查路由器、光猫或联系运营商。`);
    }
    console.log(`${divider}\n`);

    process.exit(0);
}

// Clear log
fs.writeFileSync(LOG_FILE, '');
log(`Monitor started — HTTP check on ${TARGETS.map(t => t.name).join(', ')}, duration: ${DURATION_MIN} min`);

setInterval(checkAll, INTERVAL);
setTimeout(finalReport, DURATION_MIN * 60 * 1000);
