(function () {
    const printedBy = document.body.dataset.currentUser || 'System user';
    const brandName = 'FakeArab Shawarma';
    const brandLocation = 'Montalban, Rizal';

    const text = node => node ? node.textContent.replace(/\s+/g, ' ').trim() : '';
    const csvCell = value => '"' + String(value ?? '').replace(/\s+/g, ' ').trim().replace(/"/g, '""') + '"';
    const safeFilePart = value => String(value || 'Report').normalize('NFKD').replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_|_$/g, '') || 'Report';
    const printedAt = () => new Intl.DateTimeFormat('en-PH', { dateStyle: 'long', timeStyle: 'short', timeZone: 'Asia/Manila' }).format(new Date());

    function getScope() {
        const select = document.getElementById('export-scope');
        return select ? { value: select.value, label: text(select.options[select.selectedIndex]) } : { value: 'all', label: 'All records' };
    }

    function getDateRange() {
        const select = document.querySelector('[data-export-date-range]');
        const mode = select?.value || 'all';
        const custom = document.querySelector('[data-export-custom-range]');
        if (custom) custom.style.display = mode === 'custom' ? 'flex' : 'none';
        if (mode === 'all') return { mode, start: null, end: null, label: 'All Dates' };
        let start, end, label;
        const now = new Date();
        start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        if (mode === 'today') {
            end = new Date(start); end.setDate(end.getDate() + 1); label = 'Today';
        } else if (mode === 'week') {
            start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
            end = new Date(start); end.setDate(end.getDate() + 7); label = 'This Week';
        } else if (mode === 'month') {
            start = new Date(now.getFullYear(), now.getMonth(), 1);
            end = new Date(now.getFullYear(), now.getMonth() + 1, 1); label = 'This Month';
        } else {
            const from = document.querySelector('[data-export-from]')?.value;
            const to = document.querySelector('[data-export-to]')?.value;
            if (!from || !to) throw new Error('Choose both a start date and an end date for the custom range.');
            start = new Date(from + 'T00:00:00');
            end = new Date(to + 'T00:00:00');
            if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) throw new Error('The custom date range is invalid. Check the start and end dates.');
            end.setDate(end.getDate() + 1);
            label = `${from} to ${to}`;
        }
        return { mode, start, end, label };
    }

    function getReportData(fullDocument, period) {
        const scope = getScope();
        const targetSelect = document.querySelector('[data-export-target]');
        const target = targetSelect?.value || 'all';
        const targetLabel = targetSelect ? text(targetSelect.options[targetSelect.selectedIndex]) : '';
        if (targetLabel) scope.label = `${targetLabel} · ${scope.label}`;
        const area = fullDocument.querySelector('#printable-area') || fullDocument.querySelector('.content-scroll') || fullDocument.body;
        const search = text(document.querySelector('#sales-search, #txn-search, #history-search, #log-search')).toLowerCase();
        const status = document.getElementById('method-filter')?.value || 'all';
        const typeFilter = document.getElementById('type-filter')?.value || 'all';
        const summary = Array.from(fullDocument.querySelectorAll('#kpi-section .card-box, .kpi-grid .kpi-card')).map(card => {
            const label = text(card.querySelector('.kpi-title, .kpi-title-wrapper')) || text(Array.from(card.children).find(child => child.tagName === 'DIV'));
            const value = text(card.querySelector('.kpi-value, h2'));
            return label && value ? { label, value } : null;
        }).filter(Boolean);
        const serverDateFiltered = /\/reports\/(product-sales|inventory)\//.test(location.pathname);
        const tables = Array.from(area.querySelectorAll('table')).map(source => {
            const group = source.closest('[data-export-group]')?.dataset.exportGroup || 'all';
            if (target !== 'all' && group !== target) return null;
            const table = source.cloneNode(true);
            table.querySelectorAll('.no-print, [data-html2canvas-ignore="true"]').forEach(node => node.remove());
            const headers = Array.from(table.tHead?.rows || []).flatMap(row => Array.from(row.cells));
            const dateColumn = headers.findIndex(cell => /(date|time|recorded|created)/i.test(text(cell)));
            const hasRowDates = Array.from(table.tBodies).some(body => Array.from(body.rows).some(row => row.dataset.exportDate));
            if (period.mode !== 'all' && !serverDateFiltered && dateColumn < 0 && !hasRowDates) return null;
            const actionColumn = headers.findIndex(cell => /^actions?$/i.test(text(cell)));
            if (actionColumn >= 0) Array.from(table.rows).forEach(row => row.cells[actionColumn]?.remove());
            Array.from(table.tBodies).forEach(body => Array.from(body.rows).forEach(row => {
                const movementScope = scope.value === 'in' || scope.value === 'out' ? scope.value : typeFilter;
                if (movementScope !== 'all' && row.hasAttribute('data-type') && row.dataset.type !== movementScope) { row.remove(); return; }
                if (status !== 'all' && row.hasAttribute('data-status') && row.dataset.status !== status) { row.remove(); return; }
                if (search && !text(row).toLowerCase().includes(search)) row.remove();
                if (period.mode !== 'all' && !serverDateFiltered) {
                    const dateValue = row.dataset.exportDate || text(row.cells[dateColumn]);
                    const rowDate = /^\d{4}-\d{2}-\d{2}$/.test(dateValue) ? new Date(dateValue + 'T00:00:00') : new Date(dateValue);
                    if (Number.isNaN(rowDate.getTime()) || rowDate < period.start || rowDate >= period.end) row.remove();
                }
            }));
            const wrapper = table.closest('[data-export-group]') || table.closest('.panel, .card-box, section');
            return { title: text(wrapper?.querySelector('h2, h3, h4')), table };
        }).filter(item => item && item.table.rows.length > 0);
        return { tables, scope, summary: period.mode === 'all' ? summary : [], period };
    }

    async function fetchAllReportRows(period) {
        const url = new URL(location.href);
        url.searchParams.set('page', '1');
        url.searchParams.set('per_page', '999999');
        url.searchParams.set('inv_per_page', '999999');
        if (period.mode !== 'all') {
            const formatDate = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
            const inclusiveEnd = new Date(period.end); inclusiveEnd.setDate(inclusiveEnd.getDate() - 1);
            url.searchParams.set('export_start', formatDate(period.start));
            url.searchParams.set('export_end', formatDate(inclusiveEnd));
        }
        const response = await fetch(url.toString(), { credentials: 'same-origin' });
        if (!response.ok) throw new Error('Could not load the complete report. Refresh the page and try again.');
        return new DOMParser().parseFromString(await response.text(), 'text/html');
    }

    function addHeader(doc, title, scope, period) {
        const header = doc.createElement('header');
        header.className = 'report-header';
        const identity = doc.createElement('div');
        identity.className = 'report-identity';
        const brand = doc.createElement('div'); brand.className = 'report-brand'; brand.textContent = brandName;
        const location = doc.createElement('div'); location.textContent = `Location: ${brandLocation}`;
        identity.append(brand, location);
        const report = doc.createElement('div'); report.className = 'report-title-block';
        const reportTitle = doc.createElement('h1'); reportTitle.textContent = title;
        const reportScope = doc.createElement('span'); reportScope.className = 'report-scope'; reportScope.textContent = scope;
        report.append(reportTitle, reportScope);
        header.append(identity, report);

        const metadata = doc.createElement('section'); metadata.className = 'report-meta';
        [['Date covered', period], ['Printed on', printedAt()], ['Printed by', printedBy]].forEach(([label, value]) => {
            const item = doc.createElement('div'); item.className = 'report-meta-item';
            const key = doc.createElement('span'); key.textContent = label;
            const val = doc.createElement('strong'); val.textContent = value;
            item.append(key, val); metadata.appendChild(item);
        });
        doc.body.append(header, metadata);
    }

    function renderPrintWindow(printWindow, title, data) {
        const doc = printWindow.document;
        doc.open();
        doc.write(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Report</title><style>
            @page{size:A4 landscape;margin:14mm}*{box-sizing:border-box}body{margin:0;color:#20242b;font:12px Arial,Helvetica,sans-serif}
            .report-header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;border-bottom:2px solid #18345b;padding:0 0 16px;margin-bottom:14px}
            .report-brand{font-size:21px;font-weight:800;color:#18345b;margin-bottom:6px}.report-identity{font-size:11px;line-height:1.6;color:#5b6472}
            .report-title-block{text-align:right;max-width:55%}.report-title-block h1{font-size:23px;line-height:1.2;color:#18345b;margin:0 0 8px}
            .report-scope{display:inline-block;background:#18345b;color:white;border-radius:3px;padding:4px 8px;font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
            .report-meta{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;background:#f3f5f8;border:1px solid #e2e6ec;border-radius:5px;padding:10px 12px;margin-bottom:18px}
            .report-meta-item span{display:block;color:#687386;font-size:9px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}.report-meta-item strong{font-size:11px;color:#20242b}
            .report-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:18px}.summary-card{border:1px solid #e2e6ec;border-radius:4px;padding:10px;background:#f8f9fb}.summary-card span{display:block;color:#687386;font-size:9px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}.summary-card strong{font-size:13px;color:#20242b}
            .report-section{margin:0 0 18px;break-inside:auto}.report-section h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;background:#18345b;color:white;padding:9px 11px;margin:0;border-radius:4px 4px 0 0}
            .report-section table{width:100%;border-collapse:collapse;table-layout:auto;margin:0 0 16px}.report-section thead{display:table-header-group}
            .report-section th{background:#eef1f6;color:#28374d;text-transform:uppercase;letter-spacing:.04em;font-size:9px;text-align:left;padding:8px 9px;border-bottom:1px solid #cdd4df}
            .report-section td{padding:7px 9px;border-bottom:1px solid #e5e8ee;font-size:10px;vertical-align:top;overflow-wrap:anywhere}.report-section tbody tr:nth-child(even){background:#f8f9fb}
            .report-footer{border-top:1px solid #d9dee7;padding-top:8px;margin-top:20px;font-size:9px;color:#737d8b;text-align:center}@media print{.report-section tr{break-inside:avoid}}
        </style></head><body></body></html>`);
        doc.close();
        doc.title = title;
        addHeader(doc, title, data.scope.label, data.period.label);
        if (data.summary.length) {
            const summary = doc.createElement('section'); summary.className = 'report-summary';
            data.summary.forEach(({ label, value }) => {
                const card = doc.createElement('div'); card.className = 'summary-card';
                const caption = doc.createElement('span'); caption.textContent = label;
                const amount = doc.createElement('strong'); amount.textContent = value;
                card.append(caption, amount); summary.appendChild(card);
            });
            doc.body.appendChild(summary);
        }
        data.tables.forEach(({ title: sectionTitle, table }) => {
            const section = doc.createElement('section'); section.className = 'report-section';
            if (sectionTitle) { const heading = doc.createElement('h2'); heading.textContent = sectionTitle; section.appendChild(heading); }
            section.appendChild(doc.importNode(table, true)); doc.body.appendChild(section);
        });
        const footer = doc.createElement('footer'); footer.className = 'report-footer'; footer.textContent = `${brandName} • System-generated report`;
        doc.body.appendChild(footer);
        printWindow.focus();
        setTimeout(() => printWindow.print(), 350);
    }

    function downloadCSV(title, data) {
        const rows = [[brandName], ['Location', brandLocation], ['Report', title], ['Scope', data.scope.label], ['Date covered', data.period.label], ['Printed on', printedAt()], ['Printed by', printedBy], []];
        if (data.summary.length) {
            rows.push(['Summary']);
            data.summary.forEach(item => rows.push([item.label, item.value]));
            rows.push([]);
        }
        data.tables.forEach(({ title: sectionTitle, table }) => {
            if (sectionTitle) rows.push([sectionTitle]);
            Array.from(table.rows).forEach(row => rows.push(Array.from(row.cells).map(cell => text(cell))));
            rows.push([]);
        });
        const csv = '\ufeff' + rows.map(row => row.map(csvCell).join(',')).join('\r\n');
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = safeFilePart(`${brandName}_${title}_${new Date().toISOString().slice(0, 10)}`) + '.csv';
        document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    async function runExport(button) {
        const isPdf = button.id === 'btn-pdf-export';
        const title = text(document.querySelector('.welcome-header h1, .content-scroll h1')) || 'System Report';
        const original = button.innerHTML;
        let printWindow = null;
        button.disabled = true; button.textContent = 'Preparing…';
        try {
            const period = getDateRange();
            if (isPdf) {
                printWindow = window.open('', '_blank');
                if (!printWindow) throw new Error('Allow pop-ups for this site to print or save the report as PDF.');
                printWindow.document.write('<!doctype html><title>Preparing report…</title><p style="font:16px Arial;padding:24px">Preparing report…</p>');
                printWindow.document.close();
            }
            const fullDocument = await fetchAllReportRows(period);
            const data = getReportData(fullDocument, period);
            if (!data.tables.length) throw new Error('No report rows are available to export.');
            if (isPdf) renderPrintWindow(printWindow, title, data);
            else downloadCSV(title, data);
        } catch (error) {
            if (printWindow && !printWindow.closed) printWindow.close();
            console.error('Report export failed:', error);
            alert(error.message || 'The report could not be exported. Please try again.');
        } finally {
            button.disabled = false; button.innerHTML = original;
        }
    }

    document.addEventListener('click', function (event) {
        if (!(event.target instanceof Element)) return;
        const button = event.target.closest('#btn-pdf-export, #btn-csv-export');
        if (!button) return;
        event.preventDefault(); event.stopImmediatePropagation();
        runExport(button);
    }, true);
    const dateRangeSelect = document.querySelector('[data-export-date-range]');
    if (dateRangeSelect) dateRangeSelect.addEventListener('change', getDateRange);
})();
