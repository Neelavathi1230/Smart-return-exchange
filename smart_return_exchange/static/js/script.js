/* Smart Return & Exchange Portal - front-end behaviour */
document.addEventListener("DOMContentLoaded", function () {

    // 1. Toast notifications (flash messages)
    document.querySelectorAll(".toast").forEach(function (el) {
        new bootstrap.Toast(el).show();
    });

    // 2. Confirmation dialog for any button/link with data-confirm="message"
    var confirmModalEl = document.getElementById("confirmModal");
    if (confirmModalEl) {
        var confirmModal = new bootstrap.Modal(confirmModalEl);
        var pendingButton = null;
        document.addEventListener("click", function (e) {
            var btn = e.target.closest("[data-confirm]");
            if (!btn || btn.dataset.confirmed === "1") { return; }
            e.preventDefault();
            pendingButton = btn;
            document.getElementById("confirmMessage").textContent = btn.dataset.confirm;
            confirmModal.show();
        });
        document.getElementById("confirmYes").addEventListener("click", function () {
            if (!pendingButton) { return; }
            var btn = pendingButton;
            pendingButton = null;
            confirmModal.hide();
            btn.dataset.confirmed = "1";
            if (btn.form) { btn.form.requestSubmit(btn); }
            else if (btn.href) { window.location = btn.href; }
            setTimeout(function () { btn.dataset.confirmed = "0"; }, 500);
        });
    }

    // 3. Loading indicator when a valid form is submitted
    var overlay = document.getElementById("loadingOverlay");
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function () {
            if (form.method.toLowerCase() === "post" && form.checkValidity() && overlay) {
                overlay.classList.add("show");
            }
        });
    });
    window.addEventListener("pageshow", function (e) {           // back button
        if (e.persisted && overlay) { overlay.classList.remove("show"); }
    });

    // 4. Register form: password confirmation check
    var pw = document.getElementById("password"), pw2 = document.getElementById("confirm_password");
    if (pw && pw2) {
        var check = function () {
            pw2.setCustomValidity(pw.value !== pw2.value ? "Passwords do not match" : "");
        };
        pw.addEventListener("input", check);
        pw2.addEventListener("input", check);
    }

    // 5. Show / hide password
    document.querySelectorAll("[data-toggle-password]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var input = document.getElementById(btn.dataset.togglePassword);
            var show = input.type === "password";
            input.type = show ? "text" : "password";
            btn.innerHTML = show ? '<i class="fa-solid fa-eye-slash"></i>' : '<i class="fa-solid fa-eye"></i>';
        });
    });

    // 6. Image preview for file inputs with data-preview="#imgId"
    document.querySelectorAll("input[type=file][data-preview]").forEach(function (input) {
        input.addEventListener("change", function () {
            var img = document.querySelector(input.dataset.preview);
            var file = input.files[0];
            if (!img) { return; }
            if (file && file.type.indexOf("image/") === 0) {
                img.src = URL.createObjectURL(file);
                img.classList.remove("d-none");
            } else {
                img.classList.add("d-none");
            }
        });
    });

    // 7. Return / exchange form: show order details + eligibility
    var orderSelect = document.getElementById("orderSelect");
    if (orderSelect) {
        var kind = orderSelect.dataset.kind;                       // "return" or "exchange"
        var box = document.getElementById("eligibilityBox");
        var submitBtn = document.getElementById("submitRequest");
        var update = function () {
            var opt = orderSelect.options[orderSelect.selectedIndex];
            var orderCode = document.getElementById("orderCode");
            var purchaseDate = document.getElementById("purchaseDate");
            if (!opt || !opt.value) {
                orderCode.value = ""; purchaseDate.value = "";
                box.className = "elig-box info";
                box.innerHTML = '<i class="fa-solid fa-circle-info"></i> Select a product to check eligibility.';
                submitBtn.disabled = true;
                return;
            }
            orderCode.value = opt.dataset.code;
            purchaseDate.value = opt.dataset.date;
            var eligible = opt.dataset.eligible === "1";
            var blocked = opt.dataset.blocked;
            var noExchange = kind === "exchange" && opt.dataset.exchange !== "1";
            var days = parseInt(opt.dataset.days, 10), win = parseInt(opt.dataset.window, 10);
            if (blocked) {
                box.className = "elig-box info";
                box.innerHTML = '<i class="fa-solid fa-circle-info"></i> Request ' + blocked + " already exists for this order.";
                submitBtn.disabled = true;
            } else if (noExchange) {
                box.className = "elig-box bad";
                box.innerHTML = "&#10005; Exchange is not available for this product.";
                submitBtn.disabled = true;
            } else if (eligible) {
                box.className = "elig-box ok";
                box.innerHTML = "&#10003; Eligible for " + (kind === "return" ? "Return" : "Exchange") +
                    " &mdash; " + (win - days) + " day(s) left in the " + win + "-day window.";
                submitBtn.disabled = false;
            } else {
                box.className = "elig-box bad";
                box.innerHTML = "&#10005; Return Window Expired &mdash; purchased " + days + " days ago (window: " + win + " days).";
                submitBtn.disabled = true;
            }
        };
        orderSelect.addEventListener("change", update);
        update();
    }

    // 8. Auto-submit filter selects marked data-autosubmit
    document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
        el.addEventListener("change", function () { el.form.submit(); });
    });
});
