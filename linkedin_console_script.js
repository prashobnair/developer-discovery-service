(function() {
    console.log("🚀 Starting LinkedIn Profile Scraper for logged-in view...");

    const parseDate = (dateStr) => {
        if (!dateStr || dateStr.toLowerCase().trim().includes('present')) {
            return new Date();
        }
        try {
            return new Date(dateStr);
        } catch (e) {
            console.warn(`Could not parse date: '${dateStr}'`);
            return null;
        }
    };

    const calculateTotalExperienceDays = (jobs) => {
        if (!jobs || jobs.length === 0) return 0;
        jobs.sort((a, b) => a[0] - b[0]);
        const merged = [jobs[0]];
        for (let i = 1; i < jobs.length; i++) {
            const [current_start, current_end] = jobs[i];
            const [, last_end] = merged[merged.length - 1];
            if (current_start < last_end) {
                merged[merged.length - 1][1] = new Date(Math.max(last_end, current_end));
            } else {
                merged.push(jobs[i]);
            }
        }
        return merged.reduce((total, interval) => {
            const duration = (interval[1] - interval[0]) / (1000 * 60 * 60 * 24);
            return total + duration;
        }, 0);
    };

    const scrapeProfileFromDOM = () => {
        const experienceSection = document.getElementById('experience');
        if (!experienceSection) {
            console.error("❌ Could not find Experience section");
            return;
        }
        console.log("✅ Found experience section");

        const experienceListContainer = experienceSection.parentElement.querySelector('ul');
        if (!experienceListContainer) {
            console.error("❌ Could not find experience list container");
            return;
        }
        
        const topLevelExperienceItems = Array.from(experienceListContainer.children);
        if (topLevelExperienceItems.length === 0) {
            console.warn("⚠️ Experience list contains no items");
            return;
        }

        console.log(`✅ Found ${topLevelExperienceItems.length} experience items`);

        const experiences = [];
        const jobDates = [];

        topLevelExperienceItems.forEach((item, index) => {
            console.log(`\n--- Processing Item #${index + 1} ---`);
            
            const mainEntityContainer = item.querySelector('div[data-view-name="profile-component-entity"]');
            if (!mainEntityContainer) {
                console.log("- Skipping - no main entity container");
                return;
            }

            // Improved multi-role detection - only detect if sub-roles are actual positions
            const multiRoleList = mainEntityContainer.querySelector('.pvs-entity__sub-components ul');
            let isActualMultiRole = false;
            let roles = [];
            
            if (multiRoleList) {
                roles = multiRoleList.querySelectorAll('li');
                // Check if the first sub-item has a date range (indicates it's a position)
                const firstRoleDate = roles[0]?.querySelector('span.t-14.t-normal.t-black--light > span[aria-hidden="true"]');
                if (firstRoleDate && firstRoleDate.textContent.includes('-')) {
                    isActualMultiRole = true;
                }
            }

            if (isActualMultiRole) {
                console.log("- Detected ACTUAL multi-role company");
                const companyEl = mainEntityContainer.querySelector('a[data-field="experience_company_logo"] .hoverable-link-text.t-bold > span[aria-hidden="true"]');
                const mainCompanyName = companyEl ? companyEl.textContent.trim() : 'N/A';
                console.log(`- Main Company: ${mainCompanyName}`);
                
                roles.forEach((roleItem, roleIndex) => {
                    const titleEl = roleItem.querySelector('.hoverable-link-text.t-bold > span[aria-hidden="true"]');
                    if (!titleEl) return;

                    const title = titleEl.textContent.trim();
                    const dateEl = roleItem.querySelector('span.t-14.t-normal.t-black--light > span[aria-hidden="true"]');
                    const dateText = dateEl ? dateEl.textContent.trim() : '';
                    const [dateRange] = dateText.split('·').map(s => s.trim());
                    
                    console.log(`  -- Role #${roleIndex + 1}: Title: ${title}, Date Range: ${dateRange}`);
                    
                    const [startStr, endStr] = dateRange.split('-').map(s => s.trim());
                    const startDate = parseDate(startStr);
                    const endDate = parseDate(endStr || 'Present');
                    if (startDate && endDate) jobDates.push([startDate, endDate]);

                    experiences.push({ title, company: mainCompanyName, date_range: dateRange });
                });
            } else {
                console.log("- Processing as single role");
                const titleEl = mainEntityContainer.querySelector('.hoverable-link-text.t-bold > span[aria-hidden="true"]');
                const companyEl = mainEntityContainer.querySelector('span.t-14.t-normal > span[aria-hidden="true"]');
                const dateEl = mainEntityContainer.querySelector('span.t-14.t-normal.t-black--light > span[aria-hidden="true"]');

                if (titleEl && companyEl && dateEl) {
                    const title = titleEl.textContent.trim();
                    const companyText = companyEl.textContent.trim();
                    const [companyName] = companyText.split('·').map(s => s.trim());
                    const dateText = dateEl.textContent.trim();
                    const [dateRange] = dateText.split('·').map(s => s.trim());

                    console.log(`- Title: ${title}, Company: ${companyName}, Date Range: ${dateRange}`);

                    const [startStr, endStr] = dateRange.split('-').map(s => s.trim());
                    const startDate = parseDate(startStr);
                    const endDate = parseDate(endStr || 'Present');
                    if (startDate && endDate) jobDates.push([startDate, endDate]);

                    experiences.push({ title, company: companyName, date_range: dateRange });
                } else {
                    console.log("- Skipping - missing elements");
                }
            }
        });

        if (experiences.length === 0) {
            console.error("❌ Failed to parse any experience entries");
            return;
        }

        const totalDays = calculateTotalExperienceDays(jobDates);
        const yearsOfExperience = parseFloat((totalDays / 365.25).toFixed(1));
        const currentCompany = experiences.length > 0 ? experiences[0].company : "Not Found";

        const result = {
            years_of_experience: yearsOfExperience,
            current_company: currentCompany,
            experience_summary: experiences,
        };

        console.log("\n--- SCRAPED DATA ---");
        console.table(experiences);
        console.log("\n--- SUMMARY ---");
        console.log(`Years of Experience: ${result.years_of_experience}`);
        console.log(`Current Company: ${result.current_company}`);

        try {
            const resultString = JSON.stringify(result, null, 2);
            navigator.clipboard.writeText(resultString).then(() => {
                console.log("\n✅ Result copied to clipboard!");
            }).catch(err => {
                console.log("\n📋 Result JSON:");
                console.log(resultString);
            });
        } catch (e) {
            console.log("\n📋 Result JSON:");
            console.log(JSON.stringify(result, null, 2));
        }
    };

    // Run the scraper
    scrapeProfileFromDOM();
})();