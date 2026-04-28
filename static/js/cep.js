document.addEventListener("DOMContentLoaded", function () {
    const campoCep = document.getElementById("cep");
    if (!campoCep) return;

    campoCep.addEventListener("blur", async function () {
        const cep = this.value.replace(/\D/g, "");
        if (cep.length !== 8) return;

        try {
            const resp = await fetch(`https://viacep.com.br/ws/${cep}/json/`);
            const dados = await resp.json();
            if (dados.erro) return;

            document.getElementById("logradouro").value = dados.logradouro || "";
            document.getElementById("bairro").value = dados.bairro || "";
            document.getElementById("cidade").value = dados.localidade || "";
            document.getElementById("uf").value = dados.uf || "";
        } catch (e) {
            // sem internet ou erro — ignora
        }
    });
});
