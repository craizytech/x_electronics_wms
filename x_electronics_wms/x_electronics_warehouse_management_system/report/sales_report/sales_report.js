frappe.query_reports["Sales Report"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Data",
        },
        {
            fieldname: "item",
            label: __("Item"),
            fieldtype: "Link",
            options: "Item",
        },
        {
            fieldname: "warehouse",
            label: __("Warehouse"),
            fieldtype: "Link",
            options: "Warehouse",
        },
    ],

    get_chart_data: function(columns, result) {
        // Build a bar chart of Revenue vs COGS vs Gross Profit
        // Group by invoice for the chart
        const labels   = [];
        const revenue  = [];
        const cogs     = [];
        const profit   = [];

        result.forEach(row => {
            if (row.invoice && row.customer !== "TOTAL") {
                labels.push(row.invoice);
                revenue.push(row.amount     || 0);
                cogs.push(row.cogs          || 0);
                profit.push(row.gross_profit|| 0);
            }
        });

        return {
            data: {
                labels: labels,
                datasets: [
                    { name: "Revenue",       values: revenue },
                    { name: "COGS",          values: cogs    },
                    { name: "Gross Profit",  values: profit  },
                ]
            },
            type: "bar",
            colors: ["#2563EB", "#DC2626", "#16A34A"],
        };
    }
};
