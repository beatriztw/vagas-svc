CREATE TABLE vaga (
    id                INT           PRIMARY KEY AUTO_INCREMENT,
    nome              VARCHAR(120)  NOT NULL,
    empresa           VARCHAR(100)  NOT NULL,
    plataforma        VARCHAR(30)   NOT NULL,
    nivel             VARCHAR(6)    NOT NULL CHECK (nivel IN ('Júnior', 'Pleno', 'Sênior')),
    modalidade        VARCHAR(7)    NOT NULL CHECK (modalidade IN ('Remoto', 'Híbrido')),
    data_candidatura  DATE          NOT NULL
);
